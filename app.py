# app.py 부산항 부두배정 현황 · 데이터/크롤러 병행 · 편집/시각화
# -----------------------------------------------------------------------------
# 핵심 요약
# - 두 데이터 세트(크롤러, 업로드)를 나란히 관리하고 비교(표: 위/아래, 그래프: 좌/우).
# - 시각화는 "편집 대상(active_source)"만 사용.
# - 변경은 rerun 즉시 반영(st.rerun).
# - 조회/불러오기 직후에는 표만 보이고(시각화 숨김), "시각화하기"를 눌러야 그래프 노출(show_viz).
# -----------------------------------------------------------------------------

import time
from io import BytesIO

import streamlit as st
import pandas as pd

from crawler import collect_berth_info
from schema import normalize_df, ensure_row_id, sync_raw_with_norm
from classical_pipeline import SolverConfig, run_classical_pipeline
from ui.sidebar import build_sidebar
from ui.validation import show_validation
from ui.table import show_table
from ui.viz.origin import (
    render_origin_view,
    render_origin_view_static,
    render_origin_view_drag,
)


# -----------------------------------------------------------------------------
# 페이지/헤더
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="부산항 부두배정 현황(데이터) · 편집/시각화",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.title("⛴️ 부산항 부두배정 현황 · 데이터/크롤링 · 검증 · 시각화")


# -----------------------------------------------------------------------------
# 유틸 함수: 세션 키 보장/초기화
# -----------------------------------------------------------------------------
def _ensure_ss(key: str, default):
    """
    세션 상태(st.session_state)에 키가 없거나 None인 경우, 기본값으로 초기화합니다.
    - 모든 세션 키 초기화에 사용 (DataFrame/리스트/숫자 등)
    """
    if key not in st.session_state or st.session_state[key] is None:
        st.session_state[key] = default


def _init_all_session_keys():
    """
    앱 전역에서 사용하는 모든 세션 키를 한 번에 초기화합니다.
    - 크롤러/업로드: 원본(raw), 정규화(df), 편집버퍼(edit_df_*), 스냅샷(snapshot_*), 되돌리기(undo_*), 로그(logs_*)
    - 영역 플래그: show_viz(시각화 보이기), active_source(편집 대상)
    """
    defaults = {
        # 크롤러 세트
        "crawl_raw": pd.DataFrame(),
        "crawl_df": pd.DataFrame(),
        "edit_df_crawl": pd.DataFrame(),
        "snapshot_crawl": pd.DataFrame(),
        "undo_df_crawl": None,
        "logs_crawl": [],
        "crawl_raw_original": pd.DataFrame(),
        "crawl_df_original": pd.DataFrame(),
        # 업로드 세트
        "upload_raw": pd.DataFrame(),
        "upload_df": pd.DataFrame(),
        "edit_df_upload": pd.DataFrame(),
        "snapshot_upload": pd.DataFrame(),
        "undo_df_upload": None,
        "logs_upload": [],
        "upload_raw_original": pd.DataFrame(),
        "upload_df_original": pd.DataFrame(),
        # 플래그
        "show_viz": False,
        "active_source": "crawl",  # 기본: 크롤링
        "last_crawl_filters": None,
        "crawl_filter_summary": "",
        "edit_dirty_crawl": False,
        "edit_dirty_upload": False,
        "pending_viz_loading": False,
        "react_editor_ready_crawl": False,
        "react_editor_ready_upload": False,
        "prev_use_react_drag": False,
        "prev_active_source_for_react": "crawl",
        "pending_action": None,
        "pending_viz_loading_until": 0.0,
        "react_editor_boot_started_at_crawl": 0.0,
        "react_editor_boot_started_at_upload": 0.0,
        "react_editor_boot_notice_until_crawl": 0.0,
        "react_editor_boot_notice_until_upload": 0.0,
        "classical_meta": {},
    }
    for k, v in defaults.items():
        _ensure_ss(k, v)


def _show_pending_toast():
    """
    st.rerun 후에도 알림이 보이도록 pending_toast를 소비해 토스트를 띄웁니다.
    """
    pending = st.session_state.pop("pending_toast", None)
    if pending:
        st.toast(pending.get("msg", ""), icon=pending.get("icon", "✅"))


def _run_with_min_feedback(message: str, fn, caption: str | None = None, min_ms: int = 350):
    """짧은 작업도 로딩 안내가 즉시 보이도록 최소 표시 시간을 둡니다."""
    started = time.perf_counter()
    with st.spinner(message):
        if caption:
            st.caption(caption)
        result = fn()
        remaining = max(0.0, (min_ms / 1000.0) - (time.perf_counter() - started))
        if remaining > 0:
            time.sleep(min(remaining, 0.6))
    return result


def _to_csv_bytes(df: pd.DataFrame | None) -> bytes:
    if df is None or getattr(df, "empty", True):
        return b""
    buf = BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    return buf.getvalue()


def _queue_pending_action(kind: str, **payload):
    st.session_state["pending_action"] = {"kind": kind, **payload}
    st.rerun()


def _arm_react_boot(source: str, notice_seconds: float = 1.2):
    started = time.perf_counter()
    st.session_state[f"react_editor_ready_{source}"] = False
    st.session_state[f"react_editor_boot_started_at_{source}"] = started
    st.session_state[f"react_editor_boot_notice_until_{source}"] = started + notice_seconds


def _arm_viz_loading(duration_seconds: float = 0.35):
    now = time.perf_counter()
    st.session_state["pending_viz_loading"] = True
    st.session_state["pending_viz_loading_until"] = max(
        float(st.session_state.get("pending_viz_loading_until", 0.0) or 0.0),
        now + duration_seconds,
    )


def _process_pending_action():
    action = st.session_state.pop("pending_action", None)
    if not action:
        return

    kind = action.get("kind")
    try:
        if kind == "crawl":
            handle_crawl_fetch(action.get("crawl_filters") or {}, bool(action.get("add_dims", False)))
            return

        if kind == "load":
            upload_name = action.get("upload_name")
            upload_bytes = action.get("upload_bytes")
            handle_file_load(upload_file=None, upload_name=upload_name, upload_bytes=upload_bytes)
            return

        if kind == "viz":
            st.session_state["show_viz"] = True
            st.session_state["pending_viz_loading"] = True
            st.session_state["pending_viz_loading_until"] = time.perf_counter() + 0.35
            if bool(action.get("use_react_drag")):
                _arm_react_boot(action.get("active_source", "crawl"), notice_seconds=1.5)
            return

        if kind == "classical":
            handle_classical_run(action.get("classical_config") or {})
            return
    except Exception as e:
        label = {"crawl": "조회", "load": "불러오기", "viz": "시각화", "classical": "Classical 최적화"}.get(kind, "작업")
        st.error(f"{label} 중 오류가 발생했습니다: {e}")


# -----------------------------------------------------------------------------
# 핸들러: 데이터 획득(크롤링/업로드)
# -----------------------------------------------------------------------------
def _split_date_parts(value):
    if value is None:
        return None, None, None
    return int(value.year), int(value.month), int(value.day)


@st.cache_data(show_spinner=False, ttl=300)
def _cached_collect_berth_info(
    time_code: str,
    route: str,
    berth: str,
    company: str,
    order: str,
    add_dims: bool,
    year1,
    month1,
    day1,
    year2,
    month2,
    day2,
):
    return collect_berth_info(
        time=time_code,
        route=route,
        berth=berth,
        company=company,
        order=order,
        add_bp=True,
        add_dims=add_dims,
        year1=year1,
        month1=month1,
        day1=day1,
        year2=year2,
        month2=month2,
        day2=day2,
    )


def _format_crawl_filter_summary(crawl_filters: dict, add_dims: bool) -> str:
    period = crawl_filters.get("time_label", crawl_filters.get("time", "최근 4일"))
    if crawl_filters.get("time") == "term" and crawl_filters.get("start_date") and crawl_filters.get("end_date"):
        start = crawl_filters["start_date"]
        end = crawl_filters["end_date"]
        period = f"{period} ({start:%Y-%m-%d} ~ {end:%Y-%m-%d})"

    company = crawl_filters.get("company") or "전체"
    dims = "포함" if add_dims else "미포함"
    return (
        f"기간: {period} · 항로: {crawl_filters.get('route_label', '전체')} · "
        f"선석: {crawl_filters.get('berth_label', '전체')} · 선사: {company} · "
        f"정렬: {crawl_filters.get('order_label', '출항일시')} · 제원: {dims}"
    )


def _format_feature_summary(ctrl: dict) -> str:
    def _onoff(flag: bool) -> str:
        return "ON" if flag else "OFF"

    return (
        f"1) Search {_onoff(bool(ctrl.get('feature_search', True)))} · "
        f"2) QC 준비중 · 3) Classical 준비중 · "
        f"4) Edit {_onoff(bool(ctrl.get('feature_edit', True)))} · "
        f"5) Compare {_onoff(bool(ctrl.get('feature_compare', True)))} / "
        f"Audit {_onoff(bool(ctrl.get('feature_audit_compare', True)))}"
    )


def handle_crawl_fetch(crawl_filters: dict, add_dims: bool):
    """
    [크롤링 조회] 버튼 클릭 시 호출됩니다.
    - Selectable Period Search 조건을 collect_berth_info에 그대로 전달합니다.
    - 원본 수집 후 ensure_row_id · normalize_df 로 정규화, 세트(crawl_*)에 반영합니다.
    - 시각화는 숨김(표만 보이게) show_viz=False
    """
    if not crawl_filters:
        raise ValueError("크롤링 조회 조건이 없습니다.")

    start_date = crawl_filters.get("start_date")
    end_date = crawl_filters.get("end_date")
    if crawl_filters.get("time") == "term":
        if start_date is None or end_date is None:
            raise ValueError("직접 기간 선택에서는 시작일과 종료일을 모두 입력해야 합니다.")
        if start_date > end_date:
            raise ValueError("직접 기간 선택에서는 시작일이 종료일보다 늦을 수 없습니다.")

    year1, month1, day1 = _split_date_parts(start_date)
    year2, month2, day2 = _split_date_parts(end_date)

    def _work():
        raw = _cached_collect_berth_info(
            time_code=crawl_filters["time"],
            route=crawl_filters["route"],
            berth=crawl_filters["berth"],
            company=crawl_filters.get("company", ""),
            order=crawl_filters["order"],
            add_dims=add_dims,
            year1=year1,
            month1=month1,
            day1=day1,
            year2=year2,
            month2=month2,
            day2=day2,
        )
        raw = ensure_row_id(raw)
        norm = ensure_row_id(normalize_df(raw))

        st.session_state["crawl_raw"] = raw.copy()
        st.session_state["crawl_df"] = norm.copy()
        st.session_state["crawl_raw_original"] = raw.copy()
        st.session_state["crawl_df_original"] = norm.copy()
        st.session_state["edit_df_crawl"] = norm.copy()
        st.session_state["snapshot_crawl"] = norm.copy()
        st.session_state["undo_df_crawl"] = None
        st.session_state["logs_crawl"] = []
        st.session_state["edit_dirty_crawl"] = False
        st.session_state["last_crawl_filters"] = crawl_filters.copy()
        st.session_state["crawl_filter_summary"] = _format_crawl_filter_summary(crawl_filters, add_dims=add_dims)

        st.session_state["active_source"] = "crawl"
        st.session_state["show_viz"] = False
        st.session_state["react_editor_ready_crawl"] = False
        st.success(f"조회 완료: 원본 {len(raw)}건 / 정규화 {len(norm)}건")

    _run_with_min_feedback(
        "조회 조건에 맞는 크롤링 데이터를 불러오는 중입니다...",
        _work,
        caption="잠시만 기다려 주세요. 조회가 끝나면 자동으로 결과 표가 갱신됩니다.",
        min_ms=350,
    )


def handle_file_load(upload_file=None, upload_name: str | None = None, upload_bytes: bytes | None = None):
    """
    [불러오기] 버튼 클릭 시 호출됩니다.
    - 업로드 원본 로드(CSV/XLSX) · ensure_row_id · normalize_df · 세트(upload_*) 반영
    - 시각화는 숨김(표만 보이게) show_viz=False
    """
    if upload_file is None and upload_bytes is None:
        st.warning("먼저 CSV/XLSX 파일을 업로드하세요.")
        return

    def _work():
        if upload_bytes is not None:
            file_obj = BytesIO(upload_bytes)
            file_name = upload_name or "uploaded.csv"
        else:
            file_obj = upload_file
            file_name = getattr(upload_file, "name", upload_name or "uploaded.csv")

        if str(file_name).endswith(".xlsx"):
            raw = pd.read_excel(file_obj)
        else:
            raw = pd.read_csv(file_obj)

        raw = ensure_row_id(raw)
        norm = ensure_row_id(normalize_df(raw))

        st.session_state["upload_raw"] = raw.copy()
        st.session_state["upload_df"] = norm.copy()
        st.session_state["upload_raw_original"] = raw.copy()
        st.session_state["upload_df_original"] = norm.copy()
        st.session_state["edit_df_upload"] = norm.copy()
        st.session_state["snapshot_upload"] = norm.copy()
        st.session_state["undo_df_upload"] = None
        st.session_state["logs_upload"] = []
        st.session_state["edit_dirty_upload"] = False

        st.session_state["show_viz"] = False
        st.session_state["react_editor_ready_upload"] = False
        st.success(f"파일 불러오기 완료: 원본 {len(raw)}건 / 정규화 {len(norm)}건")

    _run_with_min_feedback(
        "업로드 파일을 읽고 정규화하는 중입니다...",
        _work,
        caption="잠시만 기다려 주세요. 불러오기가 끝나면 비교에 바로 사용할 수 있습니다.",
        min_ms=350,
    )


def handle_classical_run(classical_config: dict):
    """
    [Classical 최적화 실행] 버튼 클릭 시 호출됩니다.
    - 현재 crawl_df를 입력으로 Gurobi 기반 rolling-horizon 최적화를 수행
    - 결과를 기존 시각화 스키마(df)로 변환해 crawl 세트에 반영
    """
    src_df = st.session_state.get("crawl_df", pd.DataFrame())
    if src_df.empty:
        raise ValueError("클래식 최적화 대상(crawl_df)이 없습니다. 먼저 조회를 실행하세요.")

    cfg = SolverConfig(
        slot_minutes=int(classical_config.get("slot_minutes", 60)),
        window_size=int(classical_config.get("window_size", 24)),
        overlap=int(classical_config.get("overlap", 8)),
        time_limit_sec=int(classical_config.get("time_limit_sec", 100)),
        default_vessel_length=int(classical_config.get("default_vessel_length", 150)),
        use_qcap=bool(classical_config.get("use_qcap", True)),
    )

    def _work():
        optimized_df, meta = run_classical_pipeline(src_df, cfg)
        optimized_df = ensure_row_id(normalize_df(optimized_df))

        st.session_state["crawl_df"] = optimized_df.copy()
        st.session_state["edit_df_crawl"] = optimized_df.copy()
        st.session_state["snapshot_crawl"] = optimized_df.copy()
        st.session_state["undo_df_crawl"] = None
        st.session_state["logs_crawl"] = []
        st.session_state["edit_dirty_crawl"] = False
        st.session_state["classical_meta"] = meta

        st.session_state["active_source"] = "crawl"
        st.session_state["show_viz"] = True
        st.session_state["pending_viz_loading"] = True
        st.session_state["pending_viz_loading_until"] = time.perf_counter() + 0.35
        st.success(
            f"Classical 최적화 완료: 입력 {meta.get('n_in', 0)}건 / 결과 {meta.get('n_out', 0)}건 "
            f"({meta.get('status', '-')})"
        )

    _run_with_min_feedback(
        "Classical(Gurobi) 최적화를 실행하는 중입니다...",
        _work,
        caption="모델 크기에 따라 시간이 걸릴 수 있습니다.",
        min_ms=350,
    )


# -----------------------------------------------------------------------------
# 편집 컨텍스트 바인딩/복제
# -----------------------------------------------------------------------------
def _bind_edit_context(source: str):
    """
    편집 대상 세트(source: 'crawl'|'upload')를 공용 키로 바인딩합니다.
    - render_origin_view/drag에서 edit_df / orig_df_snapshot / undo_df / edit_logs 키를 사용하므로,
      선택된 세트의 버퍼/스냅샷/되돌리기/로그를 공용 키로 매핑합니다.
    """
    if source == "crawl":
        st.session_state["edit_df"] = st.session_state["edit_df_crawl"].copy()
        st.session_state["orig_df_snapshot"] = st.session_state["snapshot_crawl"].copy()
        undo_buf = st.session_state.get("undo_df_crawl")
        st.session_state["undo_df"] = None if undo_buf is None else undo_buf.copy()
        st.session_state["edit_logs"] = list(st.session_state.get("logs_crawl", []))
    else:
        st.session_state["edit_df"] = st.session_state["edit_df_upload"].copy()
        st.session_state["orig_df_snapshot"] = st.session_state["snapshot_upload"].copy()
        undo_buf = st.session_state.get("undo_df_upload")
        st.session_state["undo_df"] = None if undo_buf is None else undo_buf.copy()
        st.session_state["edit_logs"] = list(st.session_state.get("logs_upload", []))



def _frames_are_different(current_df: pd.DataFrame | None, snapshot_df: pd.DataFrame | None, fallback_logs=None) -> bool:
    if current_df is None or snapshot_df is None:
        return False
    try:
        return not current_df.equals(snapshot_df)
    except Exception:
        return bool(fallback_logs)



def _compute_dirty_for_source(source: str) -> bool:
    return _frames_are_different(
        st.session_state.get(f"edit_df_{source}"),
        st.session_state.get(f"snapshot_{source}"),
        fallback_logs=st.session_state.get(f"logs_{source}", []),
    )



def _sync_dirty_for_source(source: str) -> bool:
    dirty_now = _compute_dirty_for_source(source)
    st.session_state[f"edit_dirty_{source}"] = dirty_now
    return dirty_now



def _persist_edit_context(source: str):
    """
    공용 편집 키를 다시 해당 세트로 복사해 둡니다.
    - 인터랙티브 시각화에서 사용자가 이동/드래그/키 조작을 하면 edit_df 값이 갱신되므로,
      그 결과를 세트별 키(edit_df_* / snapshot_* / undo_df_* / logs_*)로 되돌려 반영합니다.
    - dirty flag도 실제 edit_df vs snapshot 비교 기준으로 동기화합니다.
    """
    is_dirty = _frames_are_different(
        st.session_state.get("edit_df"),
        st.session_state.get("orig_df_snapshot"),
        fallback_logs=st.session_state.get("edit_logs"),
    )

    if source == "crawl":
        st.session_state["edit_df_crawl"] = st.session_state["edit_df"].copy()
        st.session_state["snapshot_crawl"] = st.session_state["orig_df_snapshot"].copy()
        undo_buf = st.session_state.get("undo_df")
        st.session_state["undo_df_crawl"] = None if undo_buf is None else undo_buf.copy()
        st.session_state["logs_crawl"] = list(st.session_state.get("edit_logs", []))
        st.session_state["edit_dirty_crawl"] = is_dirty
    else:
        st.session_state["edit_df_upload"] = st.session_state["edit_df"].copy()
        st.session_state["snapshot_upload"] = st.session_state["orig_df_snapshot"].copy()
        undo_buf = st.session_state.get("undo_df")
        st.session_state["undo_df_upload"] = None if undo_buf is None else undo_buf.copy()
        st.session_state["logs_upload"] = list(st.session_state.get("edit_logs", []))
        st.session_state["edit_dirty_upload"] = is_dirty


# -----------------------------------------------------------------------------
# 사이드바 액션 처리: 시각화/되돌리기/저장
# -----------------------------------------------------------------------------
def handle_sidebar_actions(ctrl: dict):
    """
    사이드바의 '시각화하기/되돌리기/저장' 액션을 처리합니다.
    - 시각화하기: show_viz=True
    - 되돌리기(1회): 편집 세트 undo 복원 + 로그 1건 삭제 + 즉시 rerun
    - 저장: 편집 세트 df 반영 + raw sync + 스냅샷/로그/undo 초기화 + show_viz=True + rerun
    """
    if ctrl.get("run_viz_crawl") or ctrl.get("run_viz"):
        st.session_state["show_viz"] = True
        st.session_state["pending_viz_loading"] = True

    if ctrl.get("cmd_undo"):
        src = ctrl["active_source"]
        if src == "crawl":
            buf = st.session_state.get("undo_df_crawl")
            if buf is not None and not getattr(buf, "empty", True):
                st.session_state["edit_df_crawl"] = buf.copy()
                st.session_state["undo_df_crawl"] = None
                if st.session_state["logs_crawl"]:
                    st.session_state["logs_crawl"].pop()
                _sync_dirty_for_source("crawl")
                st.session_state["pending_toast"] = {"msg": "↩️ 되돌리기 완료(크롤링 세트)", "icon": "↩️"}
                st.info("되돌리기 완료(크롤링 데이터).")
                st.rerun()
        else:
            buf = st.session_state.get("undo_df_upload")
            if buf is not None and not getattr(buf, "empty", True):
                st.session_state["edit_df_upload"] = buf.copy()
                st.session_state["undo_df_upload"] = None
                if st.session_state["logs_upload"]:
                    st.session_state["logs_upload"].pop()
                _sync_dirty_for_source("upload")
                st.session_state["pending_toast"] = {"msg": "↩️ 되돌리기 완료(업로드 세트)", "icon": "↩️"}
                st.info("되돌리기 완료(업로드 데이터).")
                st.rerun()

    if ctrl.get("cmd_save"):
        src = ctrl["active_source"]
        if src == "crawl":
            st.session_state["crawl_df"] = st.session_state["edit_df_crawl"].copy()
            if not st.session_state["crawl_raw"].empty and "row_id" in st.session_state["crawl_raw"].columns:
                st.session_state["crawl_raw"] = sync_raw_with_norm(
                    st.session_state["crawl_raw"], st.session_state["crawl_df"]
                )
            st.session_state["snapshot_crawl"] = st.session_state["crawl_df"].copy()
            st.session_state["logs_crawl"] = []
            st.session_state["undo_df_crawl"] = None
            st.session_state["edit_dirty_crawl"] = False
            st.session_state["show_viz"] = True
            st.session_state["pending_viz_loading"] = True
            st.session_state["pending_toast"] = {"msg": "저장되었습니다 (크롤링 세트)", "icon": "💾"}
            st.success("저장 완료(크롤링 세트 반영).")
            st.rerun()
        else:
            st.session_state["upload_df"] = st.session_state["edit_df_upload"].copy()
            if not st.session_state["upload_raw"].empty and "row_id" in st.session_state["upload_raw"].columns:
                st.session_state["upload_raw"] = sync_raw_with_norm(
                    st.session_state["upload_raw"], st.session_state["upload_df"]
                )
            st.session_state["snapshot_upload"] = st.session_state["upload_df"].copy()
            st.session_state["logs_upload"] = []
            st.session_state["undo_df_upload"] = None
            st.session_state["edit_dirty_upload"] = False
            st.session_state["show_viz"] = True
            st.session_state["pending_viz_loading"] = True
            st.session_state["pending_toast"] = {"msg": "저장되었습니다 (업로드 세트)", "icon": "💾"}
            st.success("저장 완료(업로드 세트 반영).")
            st.rerun()


def _active_edit_df(source: str) -> pd.DataFrame:
    return st.session_state["edit_df_crawl"] if source == "crawl" else st.session_state["edit_df_upload"]


def _current_saved_df(source: str) -> pd.DataFrame:
    return st.session_state["crawl_df"] if source == "crawl" else st.session_state["upload_df"]


def _current_raw_df(source: str) -> pd.DataFrame:
    return st.session_state["crawl_raw"] if source == "crawl" else st.session_state["upload_raw"]


def _original_df(source: str) -> pd.DataFrame:
    return st.session_state["crawl_df_original"] if source == "crawl" else st.session_state["upload_df_original"]


def _original_raw_df(source: str) -> pd.DataFrame:
    return st.session_state["crawl_raw_original"] if source == "crawl" else st.session_state["upload_raw_original"]


def _dirty_flag(source: str) -> bool:
    return _sync_dirty_for_source(source)


def _compare_df(source: str) -> pd.DataFrame:
    return _active_edit_df(source) if _dirty_flag(source) else _current_saved_df(source)


def _same_value(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    try:
        if isinstance(a, (pd.Timestamp,)) or isinstance(b, (pd.Timestamp,)):
            return pd.Timestamp(a) == pd.Timestamp(b)
    except Exception:
        pass
    return a == b


def _fmt_compare_ts(value) -> str:
    if pd.isna(value):
        return "-"
    try:
        return pd.to_datetime(value).strftime("%m-%d %H:%M")
    except Exception:
        return str(value)


def _delta_minutes(before, after):
    if pd.isna(before) or pd.isna(after):
        return None
    try:
        return int(round((pd.Timestamp(after) - pd.Timestamp(before)).total_seconds() / 60.0))
    except Exception:
        return None


def _delta_number(before, after):
    if pd.isna(before) or pd.isna(after):
        return None
    try:
        return round(float(after) - float(before), 1)
    except Exception:
        return None


def _duration_minutes(start, end):
    if pd.isna(start) or pd.isna(end):
        return None
    try:
        return int(round((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60.0))
    except Exception:
        return None


def _midpoint_shift_minutes(start_before, end_before, start_after, end_after):
    if any(pd.isna(v) for v in [start_before, end_before, start_after, end_after]):
        return None
    try:
        mid_before = pd.Timestamp(start_before) + (pd.Timestamp(end_before) - pd.Timestamp(start_before)) / 2
        mid_after = pd.Timestamp(start_after) + (pd.Timestamp(end_after) - pd.Timestamp(start_after)) / 2
        return int(round((mid_after - mid_before).total_seconds() / 60.0))
    except Exception:
        return None


def _midpoint_shift_meters(f_before, e_before, f_after, e_after):
    if any(pd.isna(v) for v in [f_before, e_before, f_after, e_after]):
        return None
    try:
        mid_before = (float(f_before) + float(e_before)) / 2.0
        mid_after = (float(f_after) + float(e_after)) / 2.0
        return round(mid_after - mid_before, 1)
    except Exception:
        return None


def _changed_row_ids(source: str) -> list[int]:
    diff_df = _build_original_compare_preview(source)
    if diff_df.empty or "row_id" not in diff_df.columns:
        return []
    out = []
    for v in diff_df["row_id"].tolist():
        try:
            out.append(int(v))
        except Exception:
            continue
    return out


def _build_changed_raw_rows(source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    base_raw = _original_raw_df(source)
    curr_raw = _current_raw_df(source)
    curr_df = _compare_df(source)

    if base_raw is None or curr_raw is None or curr_df is None:
        return pd.DataFrame(), pd.DataFrame()
    if base_raw.empty or curr_raw.empty or curr_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    changed_ids = _changed_row_ids(source)
    if not changed_ids:
        return pd.DataFrame(), pd.DataFrame()

    current_preview_raw = curr_raw.copy()
    if "row_id" in current_preview_raw.columns and "row_id" in curr_df.columns:
        current_preview_raw = sync_raw_with_norm(current_preview_raw, curr_df)

    base_changed = base_raw[base_raw["row_id"].isin(changed_ids)].copy() if "row_id" in base_raw.columns else pd.DataFrame()
    curr_changed = current_preview_raw[current_preview_raw["row_id"].isin(changed_ids)].copy() if "row_id" in current_preview_raw.columns else pd.DataFrame()

    if not base_changed.empty:
        base_changed = base_changed.sort_values("row_id").reset_index(drop=True)
    if not curr_changed.empty:
        curr_changed = curr_changed.sort_values("row_id").reset_index(drop=True)

    return base_changed, curr_changed


def _build_audit_export_frames(source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    diff_df = _build_original_compare_preview(source)
    changed_ids = _changed_row_ids(source)
    _, changed_curr_raw = _build_changed_raw_rows(source)

    if not changed_curr_raw.empty:
        return diff_df, changed_curr_raw

    curr_df = _compare_df(source)
    if curr_df is None or curr_df.empty or not changed_ids or "row_id" not in curr_df.columns:
        return diff_df, pd.DataFrame()

    curr_changed = curr_df[curr_df["row_id"].isin(changed_ids)].copy()
    if not curr_changed.empty:
        curr_changed = curr_changed.sort_values("row_id").reset_index(drop=True)
    return diff_df, curr_changed


def _build_original_compare_preview(source: str) -> pd.DataFrame:
    base = _original_df(source)
    curr = _compare_df(source)
    if base is None or curr is None or base.empty or curr.empty:
        return pd.DataFrame()

    tracked = [
        c for c in [
            "terminal", "berth", "vessel", "voyage", "start", "end",
            "bp", "f", "e", "berthing", "quarantine", "pilot"
        ]
        if c in base.columns and c in curr.columns
    ]
    base_slim = base[["row_id"] + tracked].copy()
    curr_slim = curr[["row_id"] + tracked].copy()
    merged = base_slim.merge(curr_slim, on="row_id", how="outer", suffixes=("_orig", "_curr"), indicator=True)

    rows = []
    for _, r in merged.iterrows():
        merge_state = r.get("_merge")
        changed = []
        if merge_state == "left_only":
            changed = ["현재 데이터에서 행 삭제"]
        elif merge_state == "right_only":
            changed = ["현재 데이터에 행 추가"]
        else:
            for col in tracked:
                if not _same_value(r.get(f"{col}_orig"), r.get(f"{col}_curr")):
                    changed.append(col)

        if not changed:
            continue

        vessel = r.get("vessel_curr") if pd.notna(r.get("vessel_curr")) else r.get("vessel_orig")
        voyage = r.get("voyage_curr") if pd.notna(r.get("voyage_curr")) else r.get("voyage_orig")

        duration_before = _duration_minutes(r.get("start_orig"), r.get("end_orig"))
        duration_after = _duration_minutes(r.get("start_curr"), r.get("end_curr"))

        rows.append({
            "row_id": r.get("row_id"),
            "vessel": vessel or "",
            "voyage": voyage or "",
            "원본 터미널": r.get("terminal_orig", ""),
            "현재 터미널": r.get("terminal_curr", ""),
            "원본 선석": r.get("berth_orig", ""),
            "현재 선석": r.get("berth_curr", ""),
            "원본 입항(x1)": _fmt_compare_ts(r.get("start_orig")),
            "현재 입항(x1)": _fmt_compare_ts(r.get("start_curr")),
            "Δ입항(min)": _delta_minutes(r.get("start_orig"), r.get("start_curr")),
            "원본 출항(x2)": _fmt_compare_ts(r.get("end_orig")),
            "현재 출항(x2)": _fmt_compare_ts(r.get("end_curr")),
            "Δ출항(min)": _delta_minutes(r.get("end_orig"), r.get("end_curr")),
            "원본 BP": r.get("bp_orig", ""),
            "현재 BP": r.get("bp_curr", ""),
            "ΔBP(m)": _delta_number(r.get("bp_orig"), r.get("bp_curr")),
            "원본 F(y1)": r.get("f_orig", ""),
            "현재 F(y1)": r.get("f_curr", ""),
            "ΔF(m)": _delta_number(r.get("f_orig"), r.get("f_curr")),
            "원본 E(y2)": r.get("e_orig", ""),
            "현재 E(y2)": r.get("e_curr", ""),
            "ΔE(m)": _delta_number(r.get("e_orig"), r.get("e_curr")),
            "Δ중심 X(min)": _midpoint_shift_minutes(r.get("start_orig"), r.get("end_orig"), r.get("start_curr"), r.get("end_curr")),
            "Δ중심 Y(m)": _midpoint_shift_meters(r.get("f_orig"), r.get("e_orig"), r.get("f_curr"), r.get("e_curr")),
            "Δ체류(min)": (None if duration_before is None or duration_after is None else duration_after - duration_before),
            "바뀐 항목": ", ".join(changed),
        })
    return pd.DataFrame(rows)


def _has_original_compare(source: str) -> bool:
    diff_df = _build_original_compare_preview(source)
    return not diff_df.empty


def render_original_compare(ctrl: dict):
    """
    선택 데이터(active_source)에 대해 '최초 조회/불러오기 원본'과 '현재 저장본/편집본'을 비교합니다.
    - Compare 토글과 분리된 Audit 토글(feature_audit_compare)로 제어합니다.
    - 변경요약에는 시간(x축), 위치(y축), 선석/BP 변화량을 함께 보여줍니다.
    - 표 비교는 전체가 아니라 변경된 선박(row_id)만 원본/현재를 나란히 보여줍니다.
    - 변경 요약 CSV / 변경 행 CSV를 바로 내보낼 수 있습니다.
    """
    if not bool(ctrl.get("feature_audit_compare", True)):
        return

    source = ctrl["active_source"]
    label = "크롤링" if source == "crawl" else "업로드"
    base_df = _original_df(source)
    curr_df = _compare_df(source)
    is_dirty = _dirty_flag(source)

    if base_df.empty or curr_df.empty:
        return

    diff_df = _build_original_compare_preview(source)

    st.subheader(f"🔎 최초 원본 대비 비교 ({label})")
    mode_label = "편집 버퍼" if is_dirty else "저장본"
    c1, c2, c3 = st.columns(3)
    c1.metric("변경 행 수", len(diff_df))
    c2.metric("비교 기준", "최초 원본")
    c3.metric("현재 비교 대상", mode_label)

    st.caption(
        "원본은 최초 조회/불러오기 시점 그대로 보존됩니다. "
        "변경 요약에는 시간(x축)·위치(y축) 변화량과 선석/BP 이동이 함께 표시됩니다. "
        "Compare를 꺼도 이 audit 패널은 별도로 유지됩니다."
    )

    if diff_df.empty:
        st.success("현재 선택 데이터는 최초 원본 대비 변경사항이 없습니다.")
        return

    changed_base_raw, changed_curr_raw = _build_changed_raw_rows(source)
    diff_export_df, changed_export_df = _build_audit_export_frames(source)

    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            "변경 요약 CSV",
            data=_to_csv_bytes(diff_export_df),
            file_name=f"{source}_audit_summary.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=diff_export_df.empty,
            help="최초 원본 대비 바뀐 row_id와 시간/위치 변화량 요약을 내려받습니다.",
        )
    with dl2:
        st.download_button(
            "변경 행 CSV",
            data=_to_csv_bytes(changed_export_df),
            file_name=f"{source}_audit_changed_rows.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=changed_export_df.empty,
            help="현재 편집 버퍼/저장본 기준의 변경 행만 내려받습니다.",
        )

    view_mode = st.radio(
        "원본 대비 보기",
        options=["변경 요약", "표 비교", "시각화 비교"],
        horizontal=True,
        key=f"origin_compare_mode_{source}",
    )

    if view_mode == "변경 요약":
        preferred_cols = [
            "row_id", "vessel", "voyage", "원본 터미널", "현재 터미널", "원본 선석", "현재 선석",
            "원본 입항(x1)", "현재 입항(x1)", "Δ입항(min)",
            "원본 출항(x2)", "현재 출항(x2)", "Δ출항(min)",
            "원본 F(y1)", "현재 F(y1)", "ΔF(m)",
            "원본 E(y2)", "현재 E(y2)", "ΔE(m)",
            "원본 BP", "현재 BP", "ΔBP(m)",
            "Δ중심 X(min)", "Δ중심 Y(m)", "Δ체류(min)", "바뀐 항목",
        ]
        show_cols = [c for c in preferred_cols if c in diff_df.columns]
        st.dataframe(diff_df[show_cols], use_container_width=True, height=320, hide_index=True)

        with st.expander("바뀐 선박의 원본/현재 행만 보기", expanded=False):
            c_left, c_right = st.columns(2)
            with c_left:
                if changed_base_raw.empty:
                    st.info("표시할 원본 행이 없습니다.")
                else:
                    show_table(changed_base_raw, f"🧾 {label} 최초 원본(변경 행만)")
            with c_right:
                if changed_curr_raw.empty:
                    st.info("표시할 현재 행이 없습니다.")
                else:
                    title = f"🧾 {label} 현재 {'편집 미리보기' if is_dirty else '저장본'} (변경 행만)"
                    show_table(changed_curr_raw, title)
        return

    if view_mode == "표 비교":
        st.caption("변경된 row_id에 해당하는 행만 원본 표와 현재 표를 나란히 보여줍니다.")
        c1, c2 = st.columns(2)
        with c1:
            if changed_base_raw.empty:
                st.info("표시할 원본 행이 없습니다.")
            else:
                show_table(changed_base_raw, f"🧾 {label} 최초 원본(변경 행만)")
        with c2:
            if changed_curr_raw.empty:
                st.info("표시할 현재 행이 없습니다.")
            else:
                title = f"🧾 {label} 현재 {'편집 미리보기' if is_dirty else '저장본'} (변경 행만)"
                show_table(changed_curr_raw, title)
        if is_dirty:
            st.info("오른쪽 표는 저장 전 편집 버퍼를 원본 컬럼 구조(raw)에 임시 반영한 미리보기입니다.")
        return

    st.caption("최초 원본과 현재 데이터를 각각 읽기 전용 타임라인으로 비교합니다.")
    c1, c2 = st.columns(2)
    with c1:
        render_origin_view_static(base_df, title_prefix=f"{label} 최초 원본")
    with c2:
        render_origin_view_static(curr_df, title_prefix=f"{label} 현재 {'편집 미리보기' if is_dirty else '저장본'}")

def _render_with_optional_loading(message: str, show_loading: bool, fn, min_ms: int = 250):
    if show_loading:
        started = time.perf_counter()
        with st.spinner(message):
            fn()
            remaining = max(0.0, (min_ms / 1000.0) - (time.perf_counter() - started))
            if remaining > 0:
                time.sleep(min(remaining, 0.45))
    else:
        fn()


def render_visualizations_and_validation(ctrl: dict):
    """
    메인 시각화 블록과 검증(편집 버퍼 기준)을 그립니다.
    - React 편집기 사용 시에는 활성 편집기만 렌더링해 rerun 비용을 줄입니다.
    - Compare 토글은 Plotly/읽기 전용 모드에서 그대로 유지됩니다.
    - 시각화 버튼 직후에는 짧은 spinner를 보여줘 사용자가 준비 중임을 알 수 있게 합니다.
    """
    has_crawl = not st.session_state["crawl_df"].empty
    has_upload = not st.session_state["upload_df"].empty
    use_react_drag = bool(ctrl.get("use_react_drag"))
    feature_edit = bool(ctrl.get("feature_edit", True))
    feature_compare = bool(ctrl.get("feature_compare", True))
    active_source = ctrl["active_source"]

    if not st.session_state["show_viz"]:
        return

    if not has_crawl and not has_upload:
        st.warning("시각화할 데이터가 없습니다. 먼저 조회하기/불러오기를 실행하세요.")
        return

    if ctrl.get("show_validation"):
        df_for_validation = _active_edit_df(active_source)
        if not df_for_validation.empty:
            show_validation("정규화 검증", df_for_validation, visible=True, location=ctrl["val_location"])

    pending_viz_loading = bool(st.session_state.get("pending_viz_loading", False))
    pending_viz_loading = pending_viz_loading or (
        time.perf_counter() < float(st.session_state.get("pending_viz_loading_until", 0.0) or 0.0)
    )

    def _render_active_source(source: str):
        df_use = st.session_state["crawl_df"] if source == "crawl" else st.session_state["upload_df"]
        if feature_edit:
            _bind_edit_context(source)
            if use_react_drag:
                render_origin_view_drag(df_use)
            else:
                render_origin_view(df_use)
            _persist_edit_context(source)
        else:
            title_prefix = "크롤링" if source == "crawl" else "업로드"
            render_origin_view_static(df_use, title_prefix=title_prefix)

    def _render_body():
        if feature_edit and use_react_drag:
            label = "크롤링" if active_source == "crawl" else "업로드"
            st.subheader(f"React 드래그 편집 ({label})")
            if has_crawl and has_upload and feature_compare:
                st.caption("React 편집 중에는 반대편 비교 시각화와 원본 표 렌더링을 잠시 생략해 드래그 성능을 우선합니다. 저장 후 비교 모드에서 다시 확인하세요.")

            react_ready_key = f"react_editor_ready_{active_source}"
            react_notice_until = float(st.session_state.get(f"react_editor_boot_notice_until_{active_source}", 0.0) or 0.0)
            react_booting = time.perf_counter() < react_notice_until
            if react_booting and not pending_viz_loading:
                st.info("React 드래그 편집기를 여는 중입니다. 준비되는 동안 로딩 안내를 먼저 보여드립니다.")
            elif not react_booting and not bool(st.session_state.get(react_ready_key, False)):
                st.session_state[react_ready_key] = True

            _render_active_source(active_source)
            return

        if has_crawl and has_upload and feature_compare:
            st.subheader("크롤링/업로드 비교 시각화 (위: 선택 데이터 · 아래: 비교 대상)")
            if active_source == "crawl":
                _render_active_source("crawl")
                st.markdown("---")
                render_origin_view_static(st.session_state["upload_df"], title_prefix="업로드")
            else:
                _render_active_source("upload")
                st.markdown("---")
                render_origin_view_static(st.session_state["crawl_df"], title_prefix="크롤링")
            return

        if has_crawl and has_upload and not feature_compare:
            selected_label = "크롤링" if active_source == "crawl" else "업로드"
            st.subheader(f"선택 데이터 시각화 ({selected_label})")
            st.caption("5) Data & Visual Comparison이 꺼져 있어 선택 데이터만 표시합니다.")
            _render_active_source(active_source)
            return

        if has_crawl:
            _render_active_source("crawl")
        else:
            _render_active_source("upload")

    try:
        _render_with_optional_loading("시각화를 준비하는 중입니다...", pending_viz_loading, _render_body)
    finally:
        if pending_viz_loading:
            st.session_state["pending_viz_loading"] = False
        st.session_state["pending_viz_loading_until"] = 0.0


# -----------------------------------------------------------------------------
# 원본 테이블 패널(읽기/쓰기 분리, 편집 1세트만 허용)
# -----------------------------------------------------------------------------
def _render_raw_panel(source_key: str, label: str, editable: bool):
    """
    원본 테이블 1패널을 렌더링합니다.
    - editable=True (편집 허용)일 때만 '수정하기/되돌리기/저장→그래프' 버튼 표시
    - 원본 수정 시: 원본 및 정규화 동기화 후 그래프/편집버퍼/스냅샷 갱신 · 즉시 리렌더
    """
    df_raw = st.session_state[f"{source_key}_raw"]
    if df_raw.empty:
        st.info(f"{label} 원본 데이터가 없습니다.")
        return

    key_prefix = f"raw_{source_key}"
    if f"{key_prefix}_mode" not in st.session_state:
        st.session_state[f"{key_prefix}_mode"] = False
    if f"{key_prefix}_buffer" not in st.session_state:
        st.session_state[f"{key_prefix}_buffer"] = df_raw.copy()
    if f"{key_prefix}_snapshot" not in st.session_state:
        st.session_state[f"{key_prefix}_snapshot"] = df_raw.copy()

    if editable:
        cols = st.columns([1, 1, 1])
        with cols[0]:
            if st.button("수정하기", disabled=st.session_state[f"{key_prefix}_mode"], use_container_width=True, key=f"editbtn-{source_key}"):
                st.session_state[f"{key_prefix}_mode"] = True
                st.session_state[f"{key_prefix}_buffer"] = df_raw.copy()
                st.session_state[f"{key_prefix}_snapshot"] = df_raw.copy()
                other = "upload" if source_key == "crawl" else "crawl"
                st.session_state[f"raw_{other}_mode"] = False

        with cols[1]:
            undo_btn = st.button("되돌리기(원본)", use_container_width=True, disabled=not st.session_state[f"{key_prefix}_mode"], key=f"undobtn-{source_key}")
        with cols[2]:
            save_btn = st.button("저장→그래프", type="primary", use_container_width=True, disabled=not st.session_state[f"{key_prefix}_mode"], key=f"savebtn-{source_key}")

        if st.session_state[f"{key_prefix}_mode"]:
            st.warning("현재 **원본 테이블 편집 모드**입니다. 그래프 편집은 잠시 중지하세요.")
            edited = st.data_editor(st.session_state[f"{key_prefix}_buffer"], use_container_width=True, height=360, key=f"editor-{source_key}")

            if undo_btn:
                st.session_state[f"{key_prefix}_buffer"] = st.session_state[f"{key_prefix}_snapshot"].copy()
                st.info("원본 되돌리기 완료.")

            if save_btn:
                st.session_state[f"{source_key}_raw"] = edited.copy()
                new_norm = ensure_row_id(normalize_df(st.session_state[f"{source_key}_raw"]))
                st.session_state[f"{source_key}_df"] = new_norm.copy()
                st.session_state[f"edit_df_{source_key}"] = new_norm.copy()
                st.session_state[f"snapshot_{source_key}"] = new_norm.copy()
                st.session_state[f"undo_df_{source_key}"] = None
                st.session_state[f"logs_{source_key}"] = []
                st.session_state[f"edit_dirty_{source_key}"] = False
                st.session_state["show_viz"] = True
                st.session_state[f"{key_prefix}_mode"] = False
                st.success(f"{label} 원본 저장 완료(그래프 갱신).")
                st.rerun()
        else:
            show_table(
                df_raw,
                f"🧾 {label} 원본",
                light_mode=bool(st.session_state.get("show_viz", False)),
                key=f"raw-{source_key}",
            )
    else:
        show_table(
            df_raw,
            f"🧾 {label} 원본 (읽기 전용)",
            light_mode=bool(st.session_state.get("show_viz", False)),
            key=f"raw-{source_key}-readonly",
        )


def render_raw_tables(ctrl: dict):
    """
    원본 테이블 UI를 그립니다.
    - React 편집 모드 + 시각화 중에는 원본 표 렌더링을 생략해 성능을 확보합니다.
    - feature_compare=True 이면 두 세트 비교, False 이면 선택 데이터만 표시합니다.
    """
    has_crawl = not st.session_state["crawl_df"].empty
    has_upload = not st.session_state["upload_df"].empty
    feature_compare = bool(ctrl.get("feature_compare", True))
    use_react_drag = bool(ctrl.get("use_react_drag"))
    feature_edit = bool(ctrl.get("feature_edit", True))

    dirty_active = _dirty_flag(ctrl["active_source"])
    if st.session_state.get("show_viz") and use_react_drag and feature_edit and dirty_active:
        st.info("React 드래그 편집 성능 최적화를 위해 저장 전 드래그 중에는 원본 표 렌더링을 생략합니다. [저장] 후에는 현재 저장본과 최초 원본을 다시 비교할 수 있습니다.")
        return

    if has_crawl and has_upload and feature_compare:
        st.subheader("🧾 원본 테이블 비교 (좌: 크롤링 / 우: 업로드)")
        c1, c2 = st.columns(2)
        with c1:
            _render_raw_panel("crawl", "크롤링", editable=(ctrl["active_source"] == "crawl"))
        with c2:
            _render_raw_panel("upload", "업로드", editable=(ctrl["active_source"] == "upload"))
    elif has_crawl and has_upload:
        src = ctrl["active_source"]
        label = "크롤링" if src == "crawl" else "업로드"
        st.subheader(f"🧾 원본 테이블 ({label})")
        st.caption("5) Data & Visual Comparison이 꺼져 있어 선택 데이터만 표시합니다.")
        _render_raw_panel(src, label, editable=True)
    elif has_crawl:
        st.subheader("🧾 원본 테이블(크롤링)")
        _render_raw_panel("crawl", "크롤링", editable=True)
    elif has_upload:
        st.subheader("🧾 원본 테이블(업로드)")
        _render_raw_panel("upload", "업로드", editable=True)
    else:
        st.info("좌측 사이드바에서 '조회하기' 또는 '불러오기'를 먼저 실행하세요.")


# -----------------------------------------------------------------------------
# 실행 흐름
# -----------------------------------------------------------------------------
def main():
    """
    앱 메인 실행 함수.
    1) 사이드바 UI 및 컨트롤 수집
    2) 세션 키 초기화
    3) 조회/불러오기 처리
    4) 사이드바 액션(시각화/되돌리기/저장) 처리
    5) 시각화(좌/우 비교) + 검증 요약
    6) 원본 테이블(좌/우 비교) 렌더
    """
    _init_all_session_keys()
    _show_pending_toast()
    _process_pending_action()
    ctrl = build_sidebar()

    if ctrl.get("run_crawl"):
        _queue_pending_action("crawl", crawl_filters=ctrl.get("crawl_filters", {}), add_dims=bool(ctrl.get("add_dims", False)))

    if ctrl.get("run_load"):
        upload_file = ctrl.get("origin_file")
        if upload_file is None:
            st.warning("먼저 CSV/XLSX 파일을 업로드하세요.")
            st.stop()
        _queue_pending_action(
            "load",
            upload_name=getattr(upload_file, "name", "uploaded.csv"),
            upload_bytes=upload_file.getvalue(),
        )

    if ctrl.get("run_viz_crawl") or ctrl.get("run_viz"):
        _queue_pending_action(
            "viz",
            active_source=ctrl.get("active_source", "crawl"),
            use_react_drag=bool(ctrl.get("use_react_drag", False)),
        )

    if ctrl.get("run_classical"):
        _queue_pending_action(
            "classical",
            classical_config=ctrl.get("classical_config", {}),
        )

    prev_use_react_drag = bool(st.session_state.get("prev_use_react_drag", False))
    prev_active_source_for_react = st.session_state.get("prev_active_source_for_react", "crawl")
    current_use_react_drag = bool(ctrl.get("use_react_drag", False))
    current_active_source = ctrl.get("active_source", "crawl")
    react_mode_changed = current_use_react_drag != prev_use_react_drag
    react_source_changed = current_use_react_drag and (prev_active_source_for_react != current_active_source)

    if current_use_react_drag and (react_mode_changed or react_source_changed):
        _arm_react_boot(current_active_source, notice_seconds=1.5)
        if st.session_state.get("show_viz"):
            _arm_viz_loading(0.45)
    elif react_mode_changed and st.session_state.get("show_viz"):
        _arm_viz_loading(0.25)

    st.session_state["prev_use_react_drag"] = current_use_react_drag
    st.session_state["prev_active_source_for_react"] = current_active_source

    _show_pending_toast()
    _process_pending_action()

    # A) 조회/불러오기
    # 버튼 클릭은 pending_action으로 큐잉되어 여기서는 이미 처리된 상태입니다.

    # B) 사이드바 액션 (시각화/되돌리기/저장)
    handle_sidebar_actions(ctrl)

    if st.session_state.get("crawl_filter_summary"):
        st.info(f"현재 조회 조건 · {st.session_state['crawl_filter_summary']}")
    if st.session_state.get("classical_meta"):
        meta = st.session_state["classical_meta"]
        st.caption(
            "Classical 결과 · "
            f"status={meta.get('status', '-')} · "
            f"in={meta.get('n_in', 0)} · out={meta.get('n_out', 0)}"
        )
    st.caption(f"연구 기능 상태 · {_format_feature_summary(ctrl)}")

    dirty_src = ctrl["active_source"]
    if _dirty_flag(dirty_src):
        dirty_label = "크롤링" if dirty_src == "crawl" else "업로드"
        st.warning(f"{dirty_label} 편집 버퍼에 저장 전 변경사항이 있습니다. 현재 그래프에는 반영되지만 원본 표에는 [저장] 후 동기화됩니다.")

    render_visualizations_and_validation(ctrl)
    render_raw_tables(ctrl)
    render_original_compare(ctrl)


if __name__ == "__main__":
    main()