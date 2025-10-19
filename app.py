import streamlit as st
import pandas as pd
import re
from io import BytesIO

# ---------- Page Setup ----------
st.set_page_config(page_title="AIOPTZ Search Term Classifier", layout="wide")
st.title("🔍 AIOPTZ Search Term Classifier")
st.write(
    """
Upload your **good_box** and **search_terms** files below (CSV or Excel).
The app will identify *Good* and *Non-Converted* search terms based on your AIOPTZ classification logic.
"""
)

# ---------- Constants ----------
FINAL_EXPORT_ORDER = [
    "brand_box",
    "brand_product_box",
    "geo_box",
    "modifier_box",
    "product_box",
    "intent_box",
    "service_box",
    "for_product_box",
    "interest_box",
    "action_box",
    "date_era_box",
    "question_box",
    "string_box",
    "non-product_box",
    "non_commerce_box",
]

# Map variations to canonical column keys
# (normalize uploaded columns: lower, spaces->_, dashes stay unless special-case)
CANONICAL_KEYS = {
    "brand_box": "brand_box",
    "brand product box": "brand_product_box",
    "brand_product_box": "brand_product_box",
    "geo_box": "geo_box",
    "modifier_box": "modifier_box",
    "product_box": "product_box",
    "intent_box": "intent_box",
    "service_box": "service_box",
    "for_product_box": "for_product_box",
    "for product box": "for_product_box",
    "interest_box": "interest_box",
    "action_box": "action_box",
    "date_era_box": "date_era_box",
    "date era box": "date_era_box",
    "question_box": "question_box",
    "string_box": "string_box",
    "non_product_box": "non-product_box",   # underscore variant -> hyphenated canonical
    "non-product_box": "non-product_box",
    "non_commerce_box": "non_commerce_box",
    "non commerce box": "non_commerce_box",
}

# ---------- Helpers ----------
def _ext(name: str) -> str:
    name = name.lower().strip()
    if name.endswith(".xlsx"): return "xlsx"
    if name.endswith(".xls"):  return "xls"
    if name.endswith(".csv"):  return "csv"
    return ""

def load_generic_table(file) -> pd.DataFrame | None:
    """
    Load CSV/XLS/XLSX with header row at the top (no special header detection).
    """
    try:
        kind = _ext(file.name)
        if kind == "csv":
            df = pd.read_csv(file)
        elif kind in ("xls", "xlsx"):
            df = pd.read_excel(file)
        else:
            st.error("❌ Unsupported file type. Please upload CSV, XLS, or XLSX.")
            return None
        return df
    except Exception as e:
        st.error(f"❌ Error reading file: {e}")
        return None

def load_search_terms_with_true_headers(file) -> pd.DataFrame | None:
    """
    Read search_terms, scanning the first ~20 rows to find the row that contains
    'search term' or 'row labels' and treat that as header row.
    Works for CSV and Excel.
    """
    try:
        kind = _ext(file.name)
        if kind == "csv":
            raw = pd.read_csv(file, header=None, dtype=str)
        elif kind in ("xls", "xlsx"):
            raw = pd.read_excel(file, header=None, dtype=str)
        else:
            st.error("❌ Unsupported search_terms file type. Use CSV/XLS/XLSX.")
            return None

        # Normalize entire raw table to strings for scanning
        raw = raw.fillna("")

        # Find header index
        header_idx = None
        scan_rows = min(len(raw), 20)
        for i in range(scan_rows):
            row_vals = raw.iloc[i].astype(str).str.strip().str.lower().tolist()
            # look for 'search term' or 'row labels' in any cell
            if any(val == "search term" for val in row_vals) or any(val == "row labels" for val in row_vals):
                header_idx = i
                break
        if header_idx is None:
            header_idx = 0  # fallback

        # Build headers
        headers = raw.iloc[header_idx].tolist()
        headers = [str(h) if h is not None else "" for h in headers]
        # Force the first column name to be "search term"
        if headers:
            headers[0] = "search term"
        # Remove "Sum of " prefix if present
        headers = [h.replace("Sum of ", "").strip() for h in headers]

        # De-duplicate headers
        seen, unique_headers = {}, []
        for h in headers:
            base = h
            if base in seen:
                seen[base] += 1
                h = f"{base}_{seen[base]}"
            else:
                seen[base] = 0
            unique_headers.append(h)

        df = raw.iloc[header_idx + 1 :].copy()
        df = df.iloc[:, : len(unique_headers)]
        df.columns = unique_headers
        # normalize the key column
        if "search term" not in df.columns:
            # sometimes capitalization/spaces differ; try to find a close match
            candidates = [c for c in df.columns if c.strip().lower().replace("_", " ") == "search term"]
            if candidates:
                df.rename(columns={candidates[0]: "search term"}, inplace=True)
        if "search term" in df.columns:
            df["search term"] = df["search term"].astype(str).str.lower().str.strip()
        else:
            st.error("❌ Could not find a 'search term' column in the uploaded search_terms file.")
            return None

        # Drop rows that are entirely empty
        df = df.replace("", pd.NA).dropna(how="all").fillna("")

        return df

    except Exception as e:
        st.error(f"❌ Error reading search_terms file: {e}")
        return None

def normalize_colname(col: str) -> str:
    """Lowercase, strip, convert spaces to underscore (keep hyphens)."""
    c = str(col).strip().lower()
    c = c.replace("  ", " ").replace(" ", "_")
    return c

def canonicalize_col(col: str) -> str | None:
    """
    Map a normalized column name to one of our canonical keys
    (e.g., 'non_product_box' -> 'non-product_box').
    """
    key = CANONICAL_KEYS.get(col)
    if key:
        return key
    # Try a few fallbacks: remove double underscores, etc.
    col2 = col.replace("__", "_").replace("-", "_")
    if col2 in CANONICAL_KEYS:
        return CANONICAL_KEYS[col2]
    return None

def normalize_word(word: str) -> str:
    word = word.lower().strip()
    if word.endswith("ies"):
        return word[:-3] + "y"
    elif word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word

def normalize_phrase(phrase: str) -> str:
    return " ".join(normalize_word(w) for w in str(phrase).lower().split())

def build_patterns(good_box_df: pd.DataFrame, ordered_cols_present: list[str]) -> list[dict]:
    patterns = []
    for _, row in good_box_df.iterrows():
        boxes = {}
        for col in ordered_cols_present:
            val = row.get(col, "")
            if isinstance(val, str) and val.strip():
                boxes[col] = normalize_phrase(val)
        if boxes:
            is_product_only = (len(boxes) == 1) and ("product_box" in boxes)
            patterns.append({"boxes": boxes, "product_only": is_product_only})
    return patterns

def matches_pattern(term: str, box_dict: dict, product_only: bool = False) -> bool:
    """
    product_only: regex 'loose exact' (allow punctuation/spacing between tokens, no extra tokens overall)
    otherwise: all phrases must appear in the term (escaped substring match).
    """
    if product_only:
        product_phrase = box_dict.get("product_box", "").strip()
        escaped = re.escape(product_phrase)
        # allow spaces, non-word chars, or underscores between tokens
        flexible = re.sub(r"\\s+", r"[\\s\\W_]+", escaped)
        pattern = r"^\s*" + flexible + r"\s*$"
        return bool(re.fullmatch(pattern, term.strip()))
    else:
        for phrase in box_dict.values():
            if not re.search(re.escape(phrase), term):
                return False
        return True

# ---------- UI: Upload ----------
st.header("📂 Upload Your Files")
good_box_file = st.file_uploader("📦 Upload good_box file (.csv, .xls, .xlsx)", type=["csv", "xls", "xlsx"])
search_terms_file = st.file_uploader("📄 Upload search_terms file (.csv, .xls, .xlsx)", type=["csv", "xls", "xlsx"])

if good_box_file and search_terms_file:
    with st.expander("👀 Preview raw uploads (first 5 rows)"):
        gb_preview = load_generic_table(good_box_file)
        st.write("**good_box preview:**")
        st.write(gb_preview.head() if gb_preview is not None and len(gb_preview) else "No preview")
        st.write("**search_terms (raw preview without header detection):**")
        st.write(load_generic_table(search_terms_file).head())

    st.success("✅ Files uploaded successfully! Click below to start classification.")

    if st.button("🚀 Run Classifier"):
        # Load search_terms with flexible header detection
        search_terms_df = load_search_terms_with_true_headers(search_terms_file)
        if search_terms_df is None:
            st.stop()

        # Load good_box as a normal table
        good_box_df = load_generic_table(good_box_file)
        if good_box_df is None or good_box_df.empty:
            st.error("❌ Could not read good_box file.")
            st.stop()

        # Normalize good_box columns and map to canonical keys
        orig_cols = list(good_box_df.columns)
        norm_cols = [normalize_colname(c) for c in orig_cols]
        mapped = []
        for c in norm_cols:
            canon = canonicalize_col(c)
            mapped.append(canon if canon else c)  # keep unknowns (won't be used)

        # Rename dataframe columns to canonical keys where known
        rename_map = {orig: new for orig, new in zip(orig_cols, mapped) if new in FINAL_EXPORT_ORDER}
        good_box_df = good_box_df.rename(columns=rename_map)

        # Keep only columns that are in our known set (present in upload)
        present_cols = [c for c in FINAL_EXPORT_ORDER if c in good_box_df.columns]
        if not present_cols:
            st.error("❌ No recognized classification columns found in good_box. Check headers.")
            st.stop()

        # Reorder to PRESENT columns (intermediate; final export will use FINAL_EXPORT_ORDER)
        good_box_df = good_box_df[present_cols]

        # ---- Build patterns from good_box ----
        patterns = build_patterns(good_box_df, present_cols)

        # ---- Classification ----
        good_records, bad_records = [], []
        total = len(search_terms_df)
        progress = st.progress(0)

        for i, (_, row) in enumerate(search_terms_df.iterrows()):
            term = str(row.get("search term", "")).strip()
            norm_term = normalize_phrase(term)
            matched = None
            for p in patterns:
                if matches_pattern(norm_term, p["boxes"], p["product_only"]):
                    matched = p["boxes"]
                    break

            if matched:
                rec = row.to_dict()
                # attach all FINAL_EXPORT_ORDER columns (fill missing with "")
                for col in FINAL_EXPORT_ORDER:
                    rec[col] = matched.get(col, "") if col in present_cols else ""
                good_records.append(rec)
            else:
                bad_records.append(row.to_dict())

            if total:
                progress.progress(int((i + 1) / total * 100))

        good_df = pd.DataFrame(good_records)
        bad_df = pd.DataFrame(bad_records)

        # Ensure final export order for classification columns (keep any other columns in front)
        if not good_df.empty:
            other_cols = [c for c in good_df.columns if c not in FINAL_EXPORT_ORDER]
            good_df = good_df[other_cols + FINAL_EXPORT_ORDER]

        # ---- Export to Excel ----
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            good_df.to_excel(writer, index=False, sheet_name="Good_Search_Terms")
            bad_df.to_excel(writer, index=False, sheet_name="Non_Converted_Terms")

        st.success("✅ Classification complete!")
        st.download_button(
            label="📥 Download Results (AIOPTZ Search Term Classifier)",
            data=output.getvalue(),
            file_name="search_term_classifier_AIOPTZ.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

else:
    st.info("⬆️ Please upload both files to begin.")
