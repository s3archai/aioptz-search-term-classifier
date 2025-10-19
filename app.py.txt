import streamlit as st
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="AIOPTZ Search Term Classifier", layout="wide")

st.title("🔍 AIOPTZ Search Term Classifier")
st.write("""
Upload your **good_box.xlsx** and **search_terms.xlsx** files below.  
The app will identify *Good* and *Non-Converted* search terms based on your AIOPTZ classification logic.
""")

# --------------------------------------
# File Upload Section
# --------------------------------------
good_box_file = st.file_uploader("📦 Upload good_box.xlsx", type=["xlsx"])
search_terms_file = st.file_uploader("📄 Upload search_terms.xlsx", type=["xlsx"])

if good_box_file and search_terms_file:
    st.success("✅ Files uploaded successfully! Click below to start classification.")
    
    if st.button("🚀 Run Classifier"):
        # --------------------------------------
        # Helper: Load Search Term Headers
        # --------------------------------------
        def load_search_terms_with_true_headers(file) -> pd.DataFrame:
            raw = pd.read_excel(file, header=None)
            header_idx = None
            for i in range(min(len(raw), 20)):
                row_vals = raw.iloc[i].astype(str).str.strip().str.lower().tolist()
                if "search term" in row_vals or "row labels" in row_vals:
                    header_idx = i
                    break
            if header_idx is None:
                header_idx = 0

            headers = raw.iloc[header_idx].tolist()
            headers = [str(h) if not pd.isna(h) else "" for h in headers]
            headers[0] = "search term"
            headers = [h.replace("Sum of ", "").strip() for h in headers]

            seen, unique_headers = {}, []
            for h in headers:
                base = h
                if base in seen:
                    seen[base] += 1
                    h = f"{base}_{seen[base]}"
                else:
                    seen[base] = 0
                unique_headers.append(h)

            df = raw.iloc[header_idx + 1:].copy()
            df = df.iloc[:, :len(unique_headers)]
            df.columns = unique_headers
            df["search term"] = df["search term"].astype(str).str.lower().str.strip()
            return df

        # --------------------------------------
        # Load Inputs
        # --------------------------------------
        search_terms_df = load_search_terms_with_true_headers(search_terms_file)
        good_box_df = pd.read_excel(good_box_file)
        good_box_df.columns = [c.lower().strip().replace(" ", "_") for c in good_box_df.columns]

        # --------------------------------------
        # Define Box Columns (15)
        # --------------------------------------
        all_boxes = [
            "geo_box", "modifier_box", "product_box", "intent_box", "service_box", "for_product_box",
            "brand_box", "brand_product_box", "interest_box",
            "action_box", "date_era_box", "question_box", "string_box",
            "non-product_box", "non_commerce_box"
        ]

        # Column Mapping
        col_map = {}
        for c in good_box_df.columns:
            base = c.replace("-", "_")
            if base in [s.replace("-", "_") for s in all_boxes]:
                if base == "non_product_box":
                    col_map[c] = "non-product_box"
                else:
                    col_map[c] = base

        present_cols = [col_map_inv for c in good_box_df.columns if c in col_map for col_map_inv in [col_map[c]]]
        ordered_cols = [c for c in all_boxes if c in present_cols]
        good_box_df = good_box_df[[next(orig for orig in good_box_df.columns if col_map.get(orig) == c) for c in ordered_cols]]
        good_box_df.columns = ordered_cols

        # --------------------------------------
        # Normalization Helpers
        # --------------------------------------
        def normalize_word(word: str) -> str:
            word = word.lower().strip()
            if word.endswith("ies"):
                return word[:-3] + "y"
            elif word.endswith("s") and not word.endswith("ss"):
                return word[:-1]
            return word

        def normalize_phrase(phrase: str) -> str:
            return " ".join(normalize_word(w) for w in str(phrase).lower().split())

        # --------------------------------------
        # Build Patterns
        # --------------------------------------
        patterns = []
        for _, row in good_box_df.iterrows():
            boxes = {col: normalize_phrase(row[col]) for col in ordered_cols if isinstance(row[col], str) and row[col].strip()}
            if boxes:
                is_product_only = len(boxes) == 1 and "product_box" in boxes
                patterns.append({"boxes": boxes, "product_only": is_product_only})

        # --------------------------------------
        # Match Function (Loose Exact Regex)
        # --------------------------------------
        def matches_pattern(term: str, box_dict: dict, product_only=False) -> bool:
            if product_only:
                product_phrase = box_dict.get("product_box", "").strip()
                escaped = re.escape(product_phrase)
                flexible_phrase = re.sub(r"\\s+", "[\\\\s\\\\W_]+", escaped)
                pattern = r"^\s*" + flexible_phrase + r"\s*$"
                return bool(re.fullmatch(pattern, term.strip()))
            else:
                for phrase in box_dict.values():
                    if not re.search(re.escape(phrase), term):
                        return False
                return True

        # --------------------------------------
        # Classification
        # --------------------------------------
        good_records, bad_records = [], []
        progress = st.progress(0)
        total = len(search_terms_df)

        for i, (_, row) in enumerate(search_terms_df.iterrows()):
            term = row["search term"]
            norm_term = normalize_phrase(term)
            matched = None
            for p in patterns:
                if matches_pattern(norm_term, p["boxes"], p["product_only"]):
                    matched = p["boxes"]
                    break

            if matched:
                rec = row.to_dict()
                for col in all_boxes:
                    rec[col] = matched.get(col, "")
                good_records.append(rec)
            else:
                bad_records.append(row.to_dict())

            progress.progress(int((i + 1) / total * 100))

        good_df = pd.DataFrame(good_records)
        bad_df = pd.DataFrame(bad_records)

        # --------------------------------------
        # Column Reordering (geo_box & intent_box)
        # --------------------------------------
        cols = list(good_df.columns)
        if "geo_box" in cols and "modifier_box" in cols:
            geo_idx = cols.index("geo_box")
            mod_idx = cols.index("modifier_box")
            if geo_idx > mod_idx:
                cols.insert(mod_idx, cols.pop(geo_idx))
        if "intent_box" in cols and "product_box" in cols:
            intent_idx = cols.index("intent_box")
            prod_idx = cols.index("product_box")
            if intent_idx < prod_idx:
                cols.insert(prod_idx + 1, cols.pop(intent_idx))
        good_df = good_df[cols]

        # --------------------------------------
        # Export to Excel
        # --------------------------------------
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            good_df.to_excel(writer, index=False, sheet_name="Good_Search_Terms")
            bad_df.to_excel(writer, index=False, sheet_name="Non_Converted_Terms")

        st.success("✅ Classification complete!")
        st.download_button(
            label="📥 Download Results (AIOPTZ Search Term Classifier)",
            data=output.getvalue(),
            file_name="search_term_classifier_AIOPTZ.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

else:
    st.info("⬆️ Please upload both files to begin.")
