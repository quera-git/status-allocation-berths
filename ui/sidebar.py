# =========================
# ui/sidebar.py
# =========================
import streamlit as st
import pandas as pd
from datetime import date, timedelta

from schema import sync_raw_with_norm


PERIOD_OPTIONS = {
    "최근 4일": "3days",
    "최근 1주": "week",
    "최근 1개월": "month",
    "직접 기간 선택": "term",
}

ROUTE_OPTIONS = {
    "전체": "ALL",
    "동남아": "EA",
    "일본": "JP",
    "중국": "CN",
}

BERTH_OPTIONS = {
    "전체": "A",
    "신항": "S",
    "감만": "G",
}

ORDER_OPTIONS = {
    "출항일시": "item1",
    "입항예정일시": "item2",
    "선석": "item3",
}


FEATURE_DEFAULTS = {
    "feature_search": True,
    "feature_qc": False,
    "feature_classical": False,
    "feature_edit": True,
    "feature_compare": True,
    "feature_audit_compare": True,
}


def _init_state():
    if "show_direct" not in st.session_state:
        st.session_state["show_direct"] = False
    if "active_source" not in st.session_state:
        st.session_state["active_source"] = "crawl"  # 기본값: 크롤링
    for key, value in FEATURE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _feature_caption(enabled: bool, ready: bool = True) -> str:
    if not ready:
        return "준비중"
    return "사용" if enabled else "숨김"


def _anchor(anchor_id: str):
    st.html(
        f'<div id="{anchor_id}" data-sb-anchor="true" style="height:0; margin:0; padding:0;"></div>'
    )


def _render_jump_assets():
    st.html(
        """
        <style>
        .sb-jump-link {
            display: block;
            text-decoration: none;
            color: inherit;
            font-weight: 600;
            line-height: 1.35;
            padding: 0.20rem 0;
            cursor: pointer;
        }
        .sb-jump-link:hover {
            color: var(--primary-color);
        }
        .sb-target-flash {
            outline: 2px solid rgba(255, 75, 75, 0.40);
            outline-offset: 4px;
            border-radius: 0.5rem;
            transition: outline-color 0.2s ease;
        }
        </style>
        <script>
        (() => {
          if (window.__sidebarJumpBound) return;
          window.__sidebarJumpBound = true;

          function findScrollableAncestor(el) {
            let parent = el?.parentElement;
            while (parent) {
              const style = window.getComputedStyle(parent);
              const oy = style.overflowY;
              if ((oy === 'auto' || oy === 'scroll') && parent.scrollHeight > parent.clientHeight) {
                return parent;
              }
              parent = parent.parentElement;
            }
            return null;
          }

          function getSidebarRoot() {
            return (
              document.querySelector('[data-testid="stSidebarUserContent"]') ||
              document.querySelector('[data-testid="stSidebar"]') ||
              document.body
            );
          }

          function findNextExpander(anchor) {
            const root = getSidebarRoot();
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            let seen = false;

            while (walker.nextNode()) {
              const node = walker.currentNode;
              if (!seen) {
                if (node === anchor) {
                  seen = true;
                }
                continue;
              }

              if (node.tagName && node.tagName.toLowerCase() === 'details') {
                return node;
              }

              if (node.matches && node.matches('[data-testid="stExpander"]')) {
                const details = node.querySelector('details');
                if (details) {
                  return details;
                }
              }
            }
            return null;
          }

          function ensureExpanderOpen(expander) {
            if (!expander) return null;
            if (!expander.open) {
              expander.open = true;
            }
            if (!expander.open) {
              const summary = expander.querySelector('summary');
              if (summary) {
                summary.click();
              }
            }
            return expander;
          }

          function scrollElementIntoSidebar(el) {
            if (!el) return;
            const scroller = findScrollableAncestor(el);
            if (scroller) {
              const targetRect = el.getBoundingClientRect();
              const scrollerRect = scroller.getBoundingClientRect();
              const nextTop = scroller.scrollTop + (targetRect.top - scrollerRect.top) - 8;
              scroller.scrollTo({ top: nextTop, behavior: 'smooth' });
            } else {
              el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          }

          function flash(el) {
            if (!el) return;
            el.classList.add('sb-target-flash');
            window.setTimeout(() => el.classList.remove('sb-target-flash'), 900);
          }

          document.addEventListener('click', (event) => {
            const link = event.target.closest('.sb-jump-link');
            if (!link) return;
            event.preventDefault();

            const targetId = link.getAttribute('data-target');
            if (!targetId) return;

            const anchor = document.getElementById(targetId);
            if (!anchor) return;

            const expander = ensureExpanderOpen(findNextExpander(anchor));
            const focusEl = expander || anchor;

            scrollElementIntoSidebar(focusEl);
            window.setTimeout(() => scrollElementIntoSidebar(focusEl), 140);
            flash(expander || anchor.parentElement || anchor);
          }, true);
        })();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def _render_jump_link(label: str, target_id: str):
    st.html(
        f'<a class="sb-jump-link" href="javascript:void(0)" data-target="{target_id}">{label}</a>'
    )


def _to_csv_bytes(df: pd.DataFrame) -> bytes:
    if df is None or getattr(df, "empty", True):
        return b""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M")
    return out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def _build_edit_export_frames(source: str):
    edit_df = st.session_state.get(f"edit_df_{source}")
    raw_df = st.session_state.get(f"{source}_raw")

    if edit_df is None or getattr(edit_df, "empty", True):
        return None, None

    norm_export = edit_df.copy()
    raw_export = None

    if raw_df is not None and not getattr(raw_df, "empty", True) and "row_id" in raw_df.columns and "row_id" in edit_df.columns:
        try:
            raw_export = sync_raw_with_norm(raw_df.copy(), edit_df.copy())
        except Exception:
            raw_export = None

    return raw_export, norm_export


# ---------------------------------------------------------
# 사이드바 레이아웃
# ---------------------------------------------------------
def build_sidebar():
    _init_state()
    with st.sidebar:
        st.header("설정")
        st.caption("A) 크롤링 데이터 조회 · 시각화  /  B) 파일 직접 불러오기 · 시각화")

        # ---------------------------------------------------------
        # 연구 기능 빠른 보기 (왼쪽 글자 클릭 → 해당 섹션으로 이동)
        # ---------------------------------------------------------
        st.subheader("연구 기능 빠른 보기")
        st.caption("왼쪽 기능명을 클릭하면 해당 섹션으로 이동하고 expander를 자동으로 펼칩니다.")
        _render_jump_assets()

        nav_rows = [
            ("1) Selectable Period Search", "sb-feature-search", "feature_search", False),
            ("2) QC-based BAP & QCAP", "sb-feature-qc", "feature_qc", True),
            ("3) Classical-based BAP & QCAP", "sb-feature-classical", "feature_classical", True),
            ("4) Drag & Drop (Edit)", "sb-feature-edit", "feature_edit", False),
            ("5) Data & Visual Comparison", "sb-feature-compare", "feature_compare", False),
        ]

        for label, anchor_id, key, disabled in nav_rows:
            c1, c2 = st.columns([6.6, 1.4], gap="small")
            with c1:
                _render_jump_link(label, anchor_id)
            with c2:
                st.toggle(
                    label,
                    key=key,
                    label_visibility="collapsed",
                    disabled=disabled,
                )

        feature_search = bool(st.session_state.get("feature_search", True))
        feature_qc = bool(st.session_state.get("feature_qc", False))
        feature_classical = bool(st.session_state.get("feature_classical", False))
        feature_edit = bool(st.session_state.get("feature_edit", True))
        feature_compare = bool(st.session_state.get("feature_compare", True))
        feature_audit_compare = bool(st.session_state.get("feature_audit_compare", True))

        st.caption(
            f"1) {_feature_caption(feature_search)} · "
            f"2) {_feature_caption(feature_qc, ready=False)} · "
            f"3) {_feature_caption(feature_classical, ready=False)} · "
            f"4) {_feature_caption(feature_edit)} · "
            f"5) 비교 {_feature_caption(feature_compare)} / Audit {_feature_caption(feature_audit_compare)}"
        )

        # ---------------------------------------------------------
        # 1) Search
        # ---------------------------------------------------------
        st.divider()
        _anchor("sb-feature-search")
        with st.expander("1) Selectable Period Search", expanded=feature_search):
            st.caption("조회 기간/항로/선석/선사/정렬 기준을 선택합니다.")

            period_label = st.selectbox(
                "조회 기간",
                options=list(PERIOD_OPTIONS.keys()),
                index=0,
            )
            time_code = PERIOD_OPTIONS[period_label]

            today = date.today()
            default_start = today - timedelta(days=3)
            default_end = today
            term_start = default_start
            term_end = default_end
            if time_code == "term":
                c1, c2 = st.columns(2)
                with c1:
                    term_start = st.date_input("시작일", value=default_start, key="crawl-term-start")
                with c2:
                    term_end = st.date_input("종료일", value=default_end, key="crawl-term-end")

            route_label = st.selectbox("항로", options=list(ROUTE_OPTIONS.keys()), index=0)
            berth_label = st.selectbox("선석 구분", options=list(BERTH_OPTIONS.keys()), index=0)
            order_label = st.selectbox("정렬 기준", options=list(ORDER_OPTIONS.keys()), index=0)
            company = st.text_input("선사 검색", value="", placeholder="예: HMM, ONE, MSC")
            add_dims = st.toggle("VesselFinder 길이/흘수 포함 (느릴 때 꺼두기)", value=False)

            crawl_filters = {
                "time": time_code,
                "time_label": period_label,
                "route": ROUTE_OPTIONS[route_label],
                "route_label": route_label,
                "berth": BERTH_OPTIONS[berth_label],
                "berth_label": berth_label,
                "company": company.strip(),
                "order": ORDER_OPTIONS[order_label],
                "order_label": order_label,
                "start_date": term_start if time_code == "term" else None,
                "end_date": term_end if time_code == "term" else None,
            }

            if time_code == "term" and term_start > term_end:
                st.warning("직접 기간 선택에서는 시작일이 종료일보다 늦을 수 없습니다.")

            col = st.columns(2)
            with col[0]:
                run_crawl = st.button("조회하기 실행", use_container_width=True)
            with col[1]:
                run_viz_crawl = st.button("시각화 하기", use_container_width=True)

        # ---------------------------------------------------------
        # B) 업로드
        # ---------------------------------------------------------
        st.divider()
        with st.expander("B) 직접 파일 열기", expanded=st.session_state["show_direct"]):
            open_direct = st.button("직접 파일 열기 ▶", use_container_width=True)
            if open_direct:
                st.session_state["show_direct"] = True

            origin_file = None
            run_load = False
            run_viz = False
            if st.session_state["show_direct"]:
                st.markdown("---")
                st.subheader("파일 불러오기")
                origin_file = st.file_uploader("CSV/XLSX 데이터 불러오기", type=["csv", "xlsx"])
                col1, col2 = st.columns(2)
                with col1:
                    run_load = st.button("불러오기 실행", use_container_width=True)
                with col2:
                    run_viz = st.button("시각화 하기", use_container_width=True)
                st.caption("추가 업로드가 없으면 아래 버튼을 클릭하세요.")
                if st.button("닫기 ✕", use_container_width=True):
                    st.session_state["show_direct"] = False
            else:
                st.caption("업로드 비교가 필요할 때만 열어 사용하세요.")
                origin_file = None
                run_load = False
                run_viz = False

        # ---------------------------------------------------------
        # 2) QC placeholder
        # ---------------------------------------------------------
        st.divider()
        _anchor("sb-feature-qc")
        with st.expander("2) QC-based BAP & QCAP", expanded=False):
            st.info("준비중입니다. 다음 단계에서 양자 최적화 입력/실행 패널을 여기에 연결합니다.")
            st.caption("예정: QUBO 생성, quantum solver 실행, objective/feasibility/runtime 비교")

        # ---------------------------------------------------------
        # 3) Classical placeholder
        # ---------------------------------------------------------
        st.divider()
        _anchor("sb-feature-classical")
        has_crawl_for_classical = bool(
            st.session_state.get("crawl_df") is not None
            and not getattr(st.session_state.get("crawl_df"), "empty", True)
        )
        with st.expander("3) Classical-based BAP & QCAP", expanded=False):
            st.caption("크롤링 결과를 입력으로 받아 Gurobi 기반 Classical BAP/QCAP 파이프라인을 실행합니다.")
            slot_minutes = st.selectbox("시간 슬롯(분)", options=[30, 60], index=1)
            window_size = st.number_input("Rolling window 크기(슬롯)", min_value=4, max_value=96, value=24, step=1)
            overlap = st.number_input("Window overlap(슬롯)", min_value=0, max_value=48, value=8, step=1)
            time_limit_sec = st.number_input("윈도우당 Time limit(초)", min_value=5, max_value=600, value=100, step=5)
            default_vessel_length = st.number_input("기본 선박 길이(m)", min_value=50, max_value=400, value=150, step=5)
            use_qcap = st.toggle("QCAP 제약 활성화(무거움)", value=True)
            st.caption("QCAP를 켜면 크레인 제약까지 포함되어 계산 시간이 크게 늘어날 수 있습니다.")

            run_classical = st.button(
                "Classical 최적화 실행",
                use_container_width=True,
                disabled=not has_crawl_for_classical,
                type="primary",
            )
            if not has_crawl_for_classical:
                st.info("먼저 1) Selectable Period Search에서 조회하기를 실행해 주세요.")

        classical_config = {
            "slot_minutes": int(slot_minutes),
            "window_size": int(window_size),
            "overlap": int(overlap),
            "time_limit_sec": int(time_limit_sec),
            "default_vessel_length": int(default_vessel_length),
            "use_qcap": bool(use_qcap),
        }

        # ---------------------------------------------------------
        # 4) Edit
        # ---------------------------------------------------------
        st.divider()
        has_crawl = bool(st.session_state.get("crawl_df") is not None and not getattr(st.session_state.get("crawl_df"), "empty", True))
        has_upload = bool(st.session_state.get("upload_df") is not None and not getattr(st.session_state.get("upload_df"), "empty", True))
        active_source = st.session_state.get("active_source", "crawl")

        _anchor("sb-feature-edit")
        with st.expander("4) Drag & Drop (Edit)", expanded=feature_edit):
            st.caption("편집기를 켜면 Plotly/React 편집이 가능하고, 끄면 읽기 전용 시각화로 봅니다. 활성화 시키고 잠시 대기해주세요")
            if not feature_edit:
                st.info("현재 Edit가 꺼져 있어 읽기 전용 시각화로 동작합니다.")

            colx = st.columns([1, 1])
            with colx[0]:
                cmd_undo = st.button("되돌리기(1회)", use_container_width=True)
            with colx[1]:
                cmd_save = st.button("저장", use_container_width=True, type="primary")

            if has_crawl and has_upload:
                src_label = st.radio(
                    "편집 대상 데이터",
                    options=["크롤링", "업로드"],
                    index=(0 if active_source == "crawl" else 1),
                    horizontal=True,
                )
                active_source = "crawl" if src_label == "크롤링" else "upload"
                st.session_state["active_source"] = active_source
            
            use_react_drag = st.toggle(
                "React 드래그 편집기 사용",
                value=False,
                disabled=not feature_edit,
                help="Plotly 그래프 대신 React 타임라인을 사용합니다. 좌우/상하 드래그로 같은 터미널 안에서 이동할 수 있으며, [저장] 후 원본 테이블에 반영됩니다.",
            )

            st.caption("현재 편집 중인 결과 전체를 CSV로 내려받을 수 있습니다. 저장 버튼을 누르지 않아도 현재 편집 버퍼 기준으로 내보냅니다.")
            raw_export_df, norm_export_df = _build_edit_export_frames(active_source)
            dl1, dl2 = st.columns(2)
            with dl1:
                st.download_button(
                    "변경본 CSV",
                    data=_to_csv_bytes(raw_export_df if raw_export_df is not None else norm_export_df),
                    file_name=f"{active_source}_edited_full.csv",
                    mime="text/csv",
                    use_container_width=True,
                    disabled=(raw_export_df is None and (norm_export_df is None or norm_export_df.empty)),
                    help="원본 컬럼 구조를 유지할 수 있으면 그 형태로, 아니면 정규화 컬럼 형태로 전체 편집본을 다운로드합니다.",
                )
            with dl2:
                st.download_button(
                    "정규화 CSV",
                    data=_to_csv_bytes(norm_export_df),
                    file_name=f"{active_source}_edited_normalized.csv",
                    mime="text/csv",
                    use_container_width=True,
                    disabled=(norm_export_df is None or norm_export_df.empty),
                    help="정규화 스키마(terminal, berth, vessel, start, end, f, e 등) 기준의 전체 편집본을 다운로드합니다.",
                )

        # ---------------------------------------------------------
        # 5) Compare
        # ---------------------------------------------------------
        st.divider()
        _anchor("sb-feature-compare")
        with st.expander("5) Data & Visual Comparison", expanded=(feature_compare or feature_audit_compare)):
            st.caption("비교 시각화/표 렌더링과, 활성 데이터의 최초 원본 대비 audit 패널을 각각 제어합니다.")

            feature_audit_compare = st.toggle(
                "최초 원본 대비 audit 패널 표시",
                key="feature_audit_compare",
                help="Compare를 꺼도 활성 데이터의 최초 원본 대비 변경사항 요약/표 비교/시각화 비교를 계속 표시합니다.",
            )

            if feature_compare:
                st.success("비교 모드가 켜져 있습니다. 크롤링/업로드를 함께 렌더링합니다.")
            else:
                st.info("비교 모드가 꺼져 있습니다. 선택된 데이터만 렌더링하여 더 가볍게 볼 수 있습니다.")

            if feature_audit_compare:
                st.success("Audit 패널이 켜져 있습니다. 활성 데이터의 최초 원본 대비 변경사항을 별도로 추적합니다.")
            else:
                st.info("Audit 패널이 꺼져 있습니다. 최초 원본 대비 비교는 숨겨집니다.")

            st.markdown(
                "- **Compare ON**: 크롤링/업로드가 모두 있을 때 비교 시각화와 비교 표를 함께 표시\n"
                "- **Compare OFF**: 활성 데이터만 표시해 렌더링 부담을 줄임\n"
                "- **Audit ON**: 활성 데이터의 **최초 원본 대비 변경사항**을 항상 별도 패널로 표시\n"
                "- **Audit OFF**: 최초 원본 대비 audit 패널을 숨김"
            )

        # ---------------------------------------------------------
        # 유효성 경고 표시 옵션
        # ---------------------------------------------------------
        st.divider()
        with st.expander("유효성 경고 표시", expanded=False):
            show_validation = st.toggle("유효성 경고 보기", value=False)
            val_location = st.radio(
                "표시 위치",
                options=["본문(상세)", "사이드바(요약)"],
                index=0,
                horizontal=True,
                disabled=not show_validation,
            )

        # ---------------------------------------------------------
        # 도움말
        # ---------------------------------------------------------
        st.divider()
        st.subheader("도움말")
        st.markdown(
            "- 상단 **기능명 텍스트**를 클릭하면 해당 섹션으로 이동하고 expander가 자동으로 열립니다.\n"
            "- 1) Selectable Period Search는 **조회 기간/항로/선석/선사/정렬**을 조합해 검색합니다.\n"
            "- 4) Drag & Drop(Edit)를 끄면 그래프는 읽기 전용으로 표시됩니다.\n"
            "- 5) Compare를 끄면 **선택 데이터만** 보여주어 렌더링이 더 가벼워집니다. Audit는 별도로 유지할 수 있습니다.\n"
            "- 두 데이터가 있을 때는 **선택 데이터**만 드래그&키 이동 가능합니다 (다른 하나는 읽기 전용).\n"
            "- 그래프 편집 후 표 데이터는 **저장해야 확정**됩니다 (저장 전에는 되돌리기 가능).\n"
            "- **저장**: 원본 테이블까지 동기화\n"
            "- **초기화는 조회하기/불러오기로** 다시 받으면 원본으로 돌아갑니다."
        )

    return {
        "add_dims": add_dims,
        "crawl_filters": crawl_filters,
        "run_crawl": run_crawl,
        "run_viz_crawl": run_viz_crawl,
        "origin_file": origin_file,
        "run_load": run_load,
        "run_viz": run_viz,
        "cmd_undo": cmd_undo,
        "cmd_save": cmd_save,
        "use_react_drag": use_react_drag,
        "show_validation": show_validation,
        "val_location": val_location,
        "active_source": active_source,
        "feature_search": feature_search,
        "feature_qc": feature_qc,
        "feature_classical": feature_classical,
        "feature_edit": feature_edit,
        "feature_compare": feature_compare,
        "feature_audit_compare": feature_audit_compare,
        "run_classical": run_classical,
        "classical_config": classical_config,
    }
