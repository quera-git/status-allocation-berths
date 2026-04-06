# =========================
# ui/table.py
# =========================
from __future__ import annotations

from io import BytesIO

import streamlit as st
import pandas as pd


def _to_csv_bytes(df: pd.DataFrame) -> bytes:
    if df is None or getattr(df, "empty", True):
        return b""
    out = BytesIO()
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out.getvalue()


# ---------------------------------------------------------
# 테이블 공통 표시
# ---------------------------------------------------------
def show_table(
    df: pd.DataFrame,
    title: str,
    *,
    light_mode: bool = False,
    key: str | None = None,
    preview_rows: int = 120,
):
    st.subheader(title)

    if df is None or getattr(df, "empty", True):
        st.info("표시할 데이터가 없습니다.")
        return

    if light_mode and len(df) > preview_rows:
        safe_key = key or title
        toggle_key = f"{safe_key}-full-table"
        download_key = f"{safe_key}-download-full"

        show_full = st.toggle(
            "전체 원본 표 렌더링",
            value=False,
            key=toggle_key,
            help="시각화와 함께 전체 원본 표를 동시에 렌더링하면 위아래 스크롤이 무거워질 수 있습니다.",
        )

        if not show_full:
            st.caption(
                f"시각화 중 스크롤 성능을 위해 상위 {preview_rows:,}행만 미리 보여줍니다. "
                f"전체 {len(df):,}행이 필요하면 위 토글을 켜세요."
            )
            st.dataframe(df.head(preview_rows), use_container_width=True, height=360)
            st.download_button(
                "전체 CSV 다운로드",
                data=_to_csv_bytes(df),
                file_name=f"{safe_key}.csv",
                mime="text/csv",
                use_container_width=True,
                key=download_key,
            )
            return

    st.dataframe(df, use_container_width=True, height=520)
