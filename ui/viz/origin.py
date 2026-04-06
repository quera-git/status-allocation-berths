# =========================
# ui/viz/origin.py
# =========================
import math
import pandas as pd
import streamlit as st
from streamlit_plotly_events import plotly_events

from ui.viz.common import period_str_kr, render_timeline_week
from schema import (
    MIN_CLEARANCE_M,
    infer_berth_from_y,
    row_center_y,
    snap_time_5min,
    snap_y_30m,
    terminal_layout,
    validate_df,
)
from streamlit_drag_timeline import drag_timeline


def render_origin_view_static(df_origin: pd.DataFrame, title_prefix: str = ""):
    """읽기 전용(드래그 없음) 주간 그래프 비교 배치용"""
    st.subheader(f"🧭 {title_prefix} 읽기 전용 타임라인(SND / GAM)")
    tab_snd, tab_gam = st.tabs(["신항 SND", "감만 GAM"])

    def _one(terminal: str):
        df_t = df_origin[df_origin["terminal"] == terminal].reset_index(drop=True)
        if df_t.empty:
            st.info(f"{terminal} 데이터가 없습니다.")
            return
        fig, (x0, x1) = render_timeline_week(df_t, terminal=terminal, title="")
        fig.update_layout(title=f"{title_prefix} {terminal} · {period_str_kr(x0, x1)}")
        fig.update_layout(width=2400, height=600)
        st.plotly_chart(fig, use_container_width=True)

    with tab_snd:
        _one("SND")
    with tab_gam:
        _one("GAM")


# ---------- 세션 상태 유틸 ----------
def _init_edit_buffers(df_norm: pd.DataFrame):
    if "edit_df" not in st.session_state:
        st.session_state["edit_df"] = df_norm.copy()
    if "orig_df_snapshot" not in st.session_state:
        st.session_state["orig_df_snapshot"] = df_norm.copy()
    if "undo_df" not in st.session_state:
        st.session_state["undo_df"] = None
    if "selected_row_id" not in st.session_state:
        st.session_state["selected_row_id"] = None
    if "edit_logs" not in st.session_state:
        st.session_state["edit_logs"] = []


def _append_log(before, after):
    st.session_state["edit_logs"].append(
        {
            "row_id": before.get("row_id"),
            "vessel": before.get("vessel", ""),
            "voyage": before.get("voyage", ""),
            "terminal_before": before.get("terminal", ""),
            "berth_before": before.get("berth", ""),
            "bp_before": before.get("bp"),
            "y_m_before": before.get("y_m"),
            "start_before": before.get("start"),
            "end_before": before.get("end"),
            "f_before": before.get("f"),
            "e_before": before.get("e"),
            "terminal_after": after.get("terminal", ""),
            "berth_after": after.get("berth", ""),
            "bp_after": after.get("bp"),
            "y_m_after": after.get("y_m"),
            "start_after": after.get("start"),
            "end_after": after.get("end"),
            "f_after": after.get("f"),
            "e_after": after.get("e"),
            "ts": pd.Timestamp.now(),
        }
    )


# ---------- 이동 스냅(5분/30m) ----------
def _is_finite_num(x) -> bool:
    try:
        v = float(x)
        return not (math.isnan(v) or math.isinf(v))
    except Exception:
        return False


def _safe_float(x, default=None):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _ts_equal(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return pd.Timestamp(a).value == pd.Timestamp(b).value


def _num_equal(a, b, eps=1e-6) -> bool:
    if not _is_finite_num(a) and not _is_finite_num(b):
        return True
    if not _is_finite_num(a) or not _is_finite_num(b):
        return False
    return abs(float(a) - float(b)) < eps


def _current_mid_y(row: pd.Series) -> float:
    mid = row_center_y(row)
    return 0.0 if mid is None else float(mid)


def _infer_terminal(default_terminal: str, target_terminal: str | None, target_berth=None) -> str:
    t = str(target_terminal or default_terminal or "").upper().strip()
    if t in {"SND", "GAM"}:
        return t
    if target_berth is not None:
        try:
            b = int(target_berth)
        except Exception:
            b = None
        if b is not None:
            if 1 <= b <= 5:
                return "SND"
            if 6 <= b <= 9:
                return "GAM"
    return str(default_terminal or "").upper().strip()


def _apply_move(
    df: pd.DataFrame,
    row_id: int,
    dmin=0,
    dy=0.0,
    *,
    target_terminal: str | None = None,
    target_berth=None,
    target_y_m=None,
    target_f=None,
    target_e=None,
) -> pd.DataFrame:
    out = df.copy()
    idx_arr = out.index[out["row_id"] == row_id]
    if len(idx_arr) == 0:
        return out
    idx = idx_arr[0]
    row = out.loc[idx]

    # 기존 값
    s0, e0 = row.get("start"), row.get("end")
    f0, e1 = row.get("f"), row.get("e")
    b0 = row.get("berth")
    t0 = row.get("terminal")
    y0 = row.get("y_m")
    bp0 = row.get("bp")

    # 후보 값(초기엔 기존값)
    s1, e2 = s0, e0
    f1, e3 = f0, e1
    b1, t1, y1, bp1 = b0, t0, y0, bp0

    changed = False

    # 시간 이동
    if dmin != 0 and (pd.notna(s0) and pd.notna(e0)):
        s1 = snap_time_5min(pd.to_datetime(s0) + pd.Timedelta(minutes=dmin))
        e2 = snap_time_5min(pd.to_datetime(e0) + pd.Timedelta(minutes=dmin))
        if (not _ts_equal(s0, s1)) or (not _ts_equal(e0, e2)):
            changed = True

    # 위치 이동: berth 자유 이동 허용(현재 terminal 내부)
    spatial_requested = (
        dy != 0
        or target_terminal is not None
        or target_berth is not None
        or target_y_m is not None
        or target_f is not None
        or target_e is not None
    )
    if spatial_requested:
        current_mid = _current_mid_y(row)
        current_len = abs(_safe_float(e1, 0.0) - _safe_float(f0, 0.0))
        if current_len <= 0:
            current_len = 10.0

        base_terminal = str(t0 or "").upper().strip()
        t1 = base_terminal if base_terminal in {"SND", "GAM"} else _infer_terminal(base_terminal, target_terminal, target_berth)
        layout = terminal_layout(t1) or terminal_layout(t0)
        if layout:
            y_max = float(layout["y_max"])
            min_mid = current_len / 2.0
            max_mid = max(min_mid, y_max - current_len / 2.0)

            if _is_finite_num(target_f) and _is_finite_num(target_e):
                raw_mid = (_safe_float(target_f, 0.0) + _safe_float(target_e, 0.0)) / 2.0
            elif _is_finite_num(target_y_m):
                raw_mid = _safe_float(target_y_m, current_mid)
            else:
                raw_mid = current_mid + float(dy)

            new_mid = snap_y_30m(min(max(raw_mid, min_mid), max_mid))
            f1 = new_mid - current_len / 2.0
            e3 = new_mid + current_len / 2.0
            y1 = new_mid
            bp1 = int(round(new_mid))
            inferred = infer_berth_from_y(t1, new_mid)
            target_berth_same_terminal = None
            if target_berth is not None:
                try:
                    tb = int(target_berth)
                    if _infer_terminal(t1, None, tb) == t1:
                        target_berth_same_terminal = tb
                except Exception:
                    target_berth_same_terminal = None
            b1 = target_berth_same_terminal if target_berth_same_terminal is not None else (inferred if inferred is not None else b0)

            if (
                (not _num_equal(f0, f1))
                or (not _num_equal(e1, e3))
                or (not _num_equal(y0, y1))
                or (not _num_equal(bp0, bp1))
                or str(t0 or "") != str(t1 or "")
                or int(b0) != int(b1)
            ):
                changed = True

    if not changed:
        return out

    before = dict(row)
    out.at[idx, "start"] = s1
    out.at[idx, "end"] = e2
    out.at[idx, "f"] = f1
    out.at[idx, "e"] = e3
    out.at[idx, "terminal"] = t1
    out.at[idx, "berth"] = b1
    out.at[idx, "y_m"] = y1
    out.at[idx, "bp"] = bp1
    after = dict(out.loc[idx])

    _append_log(before, after)
    st.session_state["undo_df"] = df.copy()
    return out


# ---------- Plotly 편집기 ----------
def render_origin_view(df_origin: pd.DataFrame):
    """
    - 중앙 라벨 클릭으로 선택
    - Shift+클릭: 선택된 막대를 해당 좌표로 이동(드래그 대신)
    - berth는 y축 위치 기준으로 자동 갱신됩니다.
    """
    _init_edit_buffers(df_origin)

    if "orig_df_snapshot" not in st.session_state or not st.session_state["orig_df_snapshot"].equals(df_origin):
        st.session_state["orig_df_snapshot"] = df_origin.copy()
        st.session_state["edit_df"] = df_origin.copy()
        st.session_state["selected_row_id"] = None
    st.subheader("🖱️ 편집 가능한 타임라인(SND / GAM)")
    st.caption("· 클릭: 선택  · Shift+클릭: 지정 위치로 이동(드롭)  · 스냅: 5분/30m · 선석은 위치에 맞춰 자동 갱신")

    tab_snd, tab_gam = st.tabs(["신항 SND", "감만 GAM"])

    def _render_one(terminal: str):
        df_all = st.session_state.get("edit_df")
        if df_all is None or not isinstance(df_all, pd.DataFrame) or df_all.empty:
            st.info(f"{terminal} 데이터가 없습니다.")
            return

        df_t = df_all[df_all["terminal"] == terminal].reset_index(drop=True)
        if df_t.empty:
            st.info(f"{terminal} 데이터가 없습니다.")
            return

        fig, (x0, x1) = render_timeline_week(df_t, terminal=terminal, title="")
        fig.update_layout(title=f"{terminal} · {period_str_kr(x0, x1)}", width=2400, height=600)

        events = plotly_events(
            fig,
            click_event=True,
            hover_event=False,
            select_event=False,
            override_height=600,
            override_width=2400,
            key=f"plotly-events-{terminal}",
        )

        target_event = None
        for ev in reversed(events or []):
            if ev.get("customdata") is not None:
                target_event = ev
                break
        if target_event is None and events:
            target_event = events[-1]

        if target_event:
            row_id = target_event.get("customdata")
            event_meta = target_event.get("event") or {}
            shift_pressed = bool(event_meta.get("shiftKey")) or bool(target_event.get("shiftKey"))

            if row_id is not None:
                st.session_state["selected_row_id"] = int(row_id)

            if shift_pressed and row_id is not None and target_event.get("x") is not None:
                rid = int(row_id)
                df_current = st.session_state["edit_df"]
                idx_arr = df_current.index[df_current["row_id"] == rid]
                if len(idx_arr):
                    idx = idx_arr[0]
                    s = pd.to_datetime(df_current.loc[idx, "start"])
                    e = pd.to_datetime(df_current.loc[idx, "end"])
                    if pd.notna(s) and pd.notna(e):
                        mid_old = s + (e - s) / 2
                        try:
                            new_x = pd.to_datetime(target_event.get("x"))
                        except Exception:
                            new_x = None

                        if new_x is not None:
                            diff_min = (new_x - mid_old).total_seconds() / 60.0
                            dmin = int(round(diff_min / 5.0) * 5)
                            y_val = target_event.get("y")
                            st.session_state["edit_df"] = _apply_move(
                                st.session_state["edit_df"],
                                rid,
                                dmin=dmin,
                                target_terminal=terminal,
                                target_y_m=y_val,
                            )

        _ = validate_df(st.session_state["edit_df"])

    with tab_snd:
        _render_one("SND")
    with tab_gam:
        _render_one("GAM")


# ---------- React 드래그 편집기 ----------
def _to_iso_ts(value):
    try:
        ts = pd.to_datetime(value)
        if pd.isna(ts):
            return None
        return ts.isoformat()
    except Exception:
        return None


def _build_drag_items(df: pd.DataFrame):
    items = []
    for row in df.itertuples(index=False):
        items.append(
            {
                "row_id": getattr(row, "row_id", None),
                "vessel": getattr(row, "vessel", ""),
                "voyage": getattr(row, "voyage", ""),
                "terminal": getattr(row, "terminal", ""),
                "berth": getattr(row, "berth", None),
                "start": _to_iso_ts(getattr(row, "start", None)),
                "end": _to_iso_ts(getattr(row, "end", None)),
                "f": _safe_float(getattr(row, "f", None)),
                "e": _safe_float(getattr(row, "e", None)),
                "y_m": _safe_float(getattr(row, "y_m", None)),
                "note": getattr(row, "note", "") or "",
                "plan_status": getattr(row, "plan_status", "") or "",
                "pilot": getattr(row, "pilot", "") or "",
            }
        )
    return items


def render_origin_view_drag(df_origin: pd.DataFrame):
    """React drag&drop 타임라인 렌더링 (선석 자유 이동 허용)"""
    _init_edit_buffers(df_origin)
    if "orig_df_snapshot" not in st.session_state or not st.session_state["orig_df_snapshot"].equals(df_origin):
        st.session_state["orig_df_snapshot"] = df_origin.copy()
        st.session_state["edit_df"] = df_origin.copy()
        st.session_state["selected_row_id"] = None

    st.subheader("🚢 신항/감만 React 드래그 편집기")
    st.caption("· 좌우 드래그: 5분 스냅 · 상하 드래그: 30m 스냅 · 같은 터미널 안에서 berth 자유 이동 · 선석은 y축 위치에 맞춰 자동 변경 · 드롭 시 한 번만 Streamlit 반영")

    df_all = st.session_state.get("edit_df")
    if df_all is None or not isinstance(df_all, pd.DataFrame) or df_all.empty:
        st.info("편집할 데이터가 없습니다. 먼저 조회/불러오기를 실행하세요.")
        return

    items = _build_drag_items(df_all)
    payload = drag_timeline(items=items) or {}

    # legacy compatibility
    if isinstance(payload, list):
        payload = {"event_id": None, "events": payload}

    src = st.session_state.get("active_source", "crawl")
    token_key = f"last_drag_event_token_{src}"
    st.session_state.setdefault(token_key, None)

    event_id = payload.get("event_id") if isinstance(payload, dict) else None
    events = payload.get("events", []) if isinstance(payload, dict) else []

    if event_id is not None and st.session_state.get(token_key) == event_id:
        events = []

    if events:
        if event_id is not None:
            st.session_state[token_key] = event_id
        for ev in events:
            rid = ev.get("row_id")
            if rid is None:
                continue
            st.session_state["edit_df"] = _apply_move(
                st.session_state["edit_df"],
                int(rid),
                dmin=int(ev.get("dmin") or 0),
                dy=float(ev.get("dy") or 0.0),
                target_terminal=ev.get("target_terminal"),
                target_berth=ev.get("target_berth"),
                target_y_m=ev.get("target_y_m"),
                target_f=ev.get("target_f"),
                target_e=ev.get("target_e"),
            )
            st.session_state["selected_row_id"] = int(rid)

        # source별 편집 상태/undo/log를 즉시 저장해야 rerun 이후에도 유지됨
        if src == "crawl":
            st.session_state["edit_df_crawl"] = st.session_state["edit_df"].copy()
            st.session_state["undo_df_crawl"] = (
                None if st.session_state.get("undo_df") is None else st.session_state["undo_df"].copy()
            )
            st.session_state["logs_crawl"] = list(st.session_state.get("edit_logs", []))
        else:
            st.session_state["edit_df_upload"] = st.session_state["edit_df"].copy()
            st.session_state["undo_df_upload"] = (
                None if st.session_state.get("undo_df") is None else st.session_state["undo_df"].copy()
            )
            st.session_state["logs_upload"] = list(st.session_state.get("edit_logs", []))
        st.rerun()

    _ = validate_df(st.session_state["edit_df"])
