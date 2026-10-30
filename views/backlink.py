from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from db import (
    add_backlink,
    bulk_insert_backlinks,
    delete_backlink,
    get_backlinks,
    get_impostazioni,
    read_csv_flexible,
    salva_esito_backlink,
    update_backlink,
)
from scraper import clean_domain, esegui_verifica_backlinks, verifica_singolo_backlink

st.header(":material/link: Monitoraggio Backlink")
st.caption(
    "Controllo automatico stato link in ingresso, codice HTTP, attributo rel (dofollow / nofollow / sponsored) e anchor text."
)


# =========================================================================
# HELPER RENDERING BADGE HTML/CSS
# =========================================================================


def _render_status_pill_html(stato_attivo: Any, checked: bool = True) -> str:
    """
    Badge circolare/pillola:
    - VERDE ('Attivo') se stato_attivo è True
    - ROSSO ('Non attivo') se stato_attivo è False
    """
    if bool(stato_attivo):
        return (
            '<span style="display:inline-flex;align-items:center;gap:6px;'
            "padding:4px 11px;border-radius:999px;background-color:#16a34a;"
            'color:#ffffff;font-weight:700;font-size:0.78rem;line-height:1.1;">'
            '<span style="width:7px;height:7px;border-radius:50%;background:#ffffff;display:inline-block;"></span>'
            "Attivo</span>"
        )
    label_txt = "Non attivo" if checked else "Non attivo (Da verificare)"
    return (
        '<span style="display:inline-flex;align-items:center;gap:6px;'
        "padding:4px 11px;border-radius:999px;background-color:#dc2626;"
        'color:#ffffff;font-weight:700;font-size:0.78rem;line-height:1.1;">'
        '<span style="width:7px;height:7px;border-radius:50%;background:#ffffff;display:inline-block;"></span>'
        f"{label_txt}</span>"
    )


def _render_http_badge_html(http_status: Any) -> str:
    """Badge compatto per il codice di stato HTTP (es. 200, 404, 403, 500)."""
    if http_status is None or pd.isna(http_status):
        return (
            '<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
            'background-color:#334155;color:#94a3b8;font-weight:700;font-size:0.78rem;">—</span>'
        )
    try:
        code = int(float(http_status))
    except (ValueError, TypeError):
        code = 0

    if code == 200:
        bg, fg = "rgba(22,163,74,0.18)", "#4ade80"
        txt = "200"
    elif code == 0:
        bg, fg = "rgba(148,163,184,0.16)", "#94a3b8"
        txt = "N/R"
    elif 300 <= code < 400:
        bg, fg = "rgba(234,88,12,0.18)", "#fb923c"
        txt = str(code)
    else:
        bg, fg = "rgba(220,38,38,0.20)", "#f87171"
        txt = str(code)

    return (
        f'<span style="display:inline-block;padding:3px 9px;border-radius:6px;'
        f'background-color:{bg};color:{fg};font-weight:700;font-size:0.8rem;">'
        f"{txt}</span>"
    )


def _render_rel_badge_html(rel_val: Any) -> str:
    """
    Badge distinto per:
    - dofollow (blu/verde)
    - nofollow (arancio)
    - sponsored / ugc (viola)
    - menzione (azzurro/ciano 'Menzione (No Link)')
    """
    rel_clean = (
        str(rel_val or "").strip().lower()
        if rel_val is not None and not pd.isna(rel_val)
        else ""
    )
    if not rel_clean:
        return (
            '<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
            'background-color:#334155;color:#94a3b8;font-weight:600;font-size:0.76rem;">—</span>'
        )

    if "menzione" in rel_clean:
        return (
            '<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
            "background-color:rgba(6,182,212,0.22);border:1px solid #06b6d4;color:#22d3ee;"
            'font-weight:700;font-size:0.75rem;">Menzione (No Link)</span>'
        )

    if "dofollow" in rel_clean:
        bg, fg = "#0284c7", "#ffffff"
    elif "nofollow" in rel_clean:
        bg, fg = "#ea580c", "#ffffff"
    elif "sponsored" in rel_clean or "ugc" in rel_clean:
        bg, fg = "#7c3aed", "#ffffff"
    else:
        bg, fg = "#475569", "#f8fafc"

    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
        f'background-color:{bg};color:{fg};font-weight:700;font-size:0.76rem;text-transform:lowercase;">'
        f"{rel_clean}</span>"
    )


def _format_check_ts(ts_val: Any) -> str:
    if not ts_val:
        return "Mai verificato"
    try:
        cleaned = str(ts_val).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%d/%m/%y %H:%M")
    except Exception:
        return str(ts_val)[:16]


# =========================================================================
# DIALOG POPUP MODIFICA ED ELIMINAZIONE BACKLINK
# =========================================================================


@st.dialog("Modifica Backlink")
def dialog_modifica_backlink(
    bl_row: Dict[str, Any], mio_dominio: str, nome_azienda: str = ""
) -> None:
    bl_id = str(bl_row["id"])
    with st.form(f"form_edit_bl_{bl_id}"):
        src_in = st.text_input(
            "URL Sorgente (pagina esterna che ci linka) *",
            value=bl_row.get("url_sorgente") or "",
        )
        dest_in = st.text_input(
            "URL Destinazione sul nostro sito (opzionale)",
            value=bl_row.get("url_destinazione") or "",
        )
        recheck_now = st.checkbox("Riverifica subito il link al salvataggio", value=True)
        sub_edit = st.form_submit_button(
            "Salva Modifiche",
            type="primary",
            icon=":material/save:",
            use_container_width=True,
        )
        if sub_edit:
            if not src_in.strip():
                st.warning("Inserisci un URL Sorgente valido.", icon=":material/warning:")
            else:
                try:
                    update_backlink(
                        bl_id,
                        {
                            "url_sorgente": src_in.strip(),
                            "url_destinazione": dest_in.strip(),
                        },
                    )
                    if recheck_now:
                        res_chk = verifica_singolo_backlink(
                            src_in.strip(),
                            dest_in.strip(),
                            mio_dominio,
                            nome_azienda=nome_azienda,
                        )
                        salva_esito_backlink(
                            backlink_id=bl_id,
                            stato_attivo=bool(res_chk.get("stato_attivo")),
                            http_status=res_chk.get("http_status"),
                            rel_attribute=res_chk.get("rel_attribute"),
                            anchor_text=res_chk.get("anchor_text"),
                            url_destinazione_trovato=res_chk.get("url_destinazione_trovato"),
                        )
                    st.toast("Backlink aggiornato!", icon=":material/check_circle:")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Errore durante il salvataggio: {exc}", icon=":material/error:")


@st.dialog("Conferma Eliminazione Backlink")
def dialog_elimina_backlink(bl_row: Dict[str, Any]) -> None:
    bl_id = str(bl_row["id"])
    src_url = bl_row.get("url_sorgente") or ""
    st.warning(
        f"Vuoi eliminare definitivamente il monitoraggio per:\n`{src_url}`?",
        icon=":material/warning:",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "Elimina",
            type="primary",
            icon=":material/delete:",
            use_container_width=True,
            key=f"confirm_del_bl_{bl_id}",
        ):
            try:
                delete_backlink(bl_id)
                st.toast("Backlink eliminato!", icon=":material/check_circle:")
                st.rerun()
            except Exception as exc:
                st.error(f"Errore eliminazione: {exc}", icon=":material/error:")
    with c2:
        if st.button("Annulla", use_container_width=True, key=f"cancel_del_bl_{bl_id}"):
            st.rerun()


# =========================================================================
# CARICAMENTO DATI E KPI BOX IN ALTO (5 COLONNE INCLUSE MENZIONI)
# =========================================================================

impostazioni = get_impostazioni()
mio_sito_web = (impostazioni.get("sito_web") or "").strip()
mio_nome_azienda = (impostazioni.get("nome_azienda") or "").strip()
mio_dominio_pulito = clean_domain(mio_sito_web)

backlinks = get_backlinks()

tot_bl = len(backlinks)
attivi_bl = sum(1 for b in backlinks if bool(b.get("stato_attivo")))
non_attivi_bl = tot_bl - attivi_bl

menzioni_count = sum(
    1
    for b in backlinks
    if bool(b.get("stato_attivo"))
    and "menzione" in str(b.get("rel_attribute") or "").lower()
)
link_attivi_puri = attivi_bl - menzioni_count

dofollow_count = sum(
    1
    for b in backlinks
    if bool(b.get("stato_attivo"))
    and "dofollow" in str(b.get("rel_attribute") or "").lower()
)
nofollow_count = sum(
    1
    for b in backlinks
    if bool(b.get("stato_attivo"))
    and any(
        k in str(b.get("rel_attribute") or "").lower()
        for k in ("nofollow", "sponsored", "ugc")
    )
)
tot_rel_classified = dofollow_count + nofollow_count
if tot_rel_classified > 0:
    pct_dofollow = round((dofollow_count / tot_rel_classified) * 100)
    pct_nofollow = 100 - pct_dofollow
    rel_ratio_str = f"{pct_dofollow}% Do / {pct_nofollow}% No"
else:
    rel_ratio_str = "0% Do / 0% No"

kpi_cols = st.columns(5)
with kpi_cols[0]:
    with st.container(border=True):
        st.caption(":material/link: **Totale Monitorati**")
        st.markdown(
            f"<div style='font-size:1.65rem;font-weight:800;color:#f8fafc;'>{tot_bl}</div>",
            unsafe_allow_html=True,
        )
with kpi_cols[1]:
    with st.container(border=True):
        st.caption(":material/check_circle: **Link Attivi**")
        st.markdown(
            f"<div style='font-size:1.65rem;font-weight:800;color:#22c55e;'>{link_attivi_puri}</div>",
            unsafe_allow_html=True,
        )
with kpi_cols[2]:
    with st.container(border=True):
        st.caption(":material/record_voice_over: **Menzioni (No Link)**")
        st.markdown(
            f"<div style='font-size:1.65rem;font-weight:800;color:#22d3ee;'>{menzioni_count}</div>",
            unsafe_allow_html=True,
        )
with kpi_cols[3]:
    with st.container(border=True):
        st.caption(":material/link_off: **Non Attivi / Persi**")
        st.markdown(
            f"<div style='font-size:1.65rem;font-weight:800;color:#ef4444;'>{non_attivi_bl}</div>",
            unsafe_allow_html=True,
        )
with kpi_cols[4]:
    with st.container(border=True):
        st.caption(":material/verified: **Dofollow vs Nofollow**")
        st.markdown(
            f"<div style='font-size:1.4rem;font-weight:800;color:#38bdf8;'>{rel_ratio_str}</div>",
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)

tab_report_bl, tab_manage_bl = st.tabs(
    [
        ":material/link: Report Backlink",
        ":material/add_link: Gestione Link (Aggiungi / CSV)",
    ]
)

# -------------------------------------------------------------------------
# TAB 1: REPORT BACKLINK (VISTA TABELLARE PRINCIPALE)
# -------------------------------------------------------------------------
with tab_report_bl:
    top_bar_l, top_bar_r = st.columns([2.2, 1.2], vertical_alignment="center")
    with top_bar_l:
        st.markdown(
            f"#### :material/fact_check: Stato del Profilo Backlink (`{mio_dominio_pulito or 'Nostro Dominio'}`)"
        )
    with top_bar_r:
        run_all_bl = st.button(
            "Verifica tutti i Backlink ora",
            icon=":material/sync:",
            type="primary",
            use_container_width=True,
            key="btn_verify_all_backlinks",
        )

    if run_all_bl:
        if not backlinks:
            st.warning(
                "Nessun backlink presente a catalogo. Aggiungine uno nel tab 'Gestione Link'.",
                icon=":material/warning:",
            )
        else:
            bl_prog = st.progress(0.0, text="Avvio verifica parallela dei backlink...")

            def _upd_bl_prog(pct: float, msg: str) -> None:
                bl_prog.progress(pct, text=msg)

            try:
                bl_stats = esegui_verifica_backlinks(progress_callback=_upd_bl_prog)
                bl_prog.empty()
                st.session_state["last_bl_scan_log"] = bl_stats.get("logs", [])
                st.toast(
                    f"Verifica completata! Attivi: {bl_stats['attivi']} / {bl_stats['totale_verificati']}",
                    icon=":material/check_circle:",
                )
                st.rerun()
            except Exception as exc:
                bl_prog.empty()
                st.error(
                    f"Errore durante la verifica dei backlink: {exc}",
                    icon=":material/error:",
                )

    if st.session_state.get("last_bl_scan_log"):
        with st.expander(":material/receipt_long: Risultati ultima verifica Backlink", expanded=False):
            for line in st.session_state["last_bl_scan_log"]:
                st.markdown(f"- {line}")
            if st.button(
                "Chiudi log Backlink",
                icon=":material/close:",
                key="btn_close_bl_scan_log",
            ):
                st.session_state.pop("last_bl_scan_log", None)
                st.rerun()

    # Filtri rapidi sopra la tabella
    flt_c1, flt_c2, flt_c3 = st.columns([1.4, 1.4, 2.0], vertical_alignment="bottom")
    with flt_c1:
        filtro_stato = st.selectbox(
            "Filtra per Stato",
            options=["Tutti", "Attivi", "Non Attivi"],
            key="flt_bl_stato",
        )
    with flt_c2:
        filtro_rel = st.selectbox(
            "Filtra per Tipo / Rel",
            options=["Tutti", "dofollow", "nofollow", "sponsored", "menzione"],
            key="flt_bl_rel",
        )
    with flt_c3:
        filtro_search = st.text_input(
            "Cerca per dominio sorgente o anchor text",
            placeholder="Es. blogirrigazione.it...",
            key="flt_bl_search",
        )

    filtered_bl: List[Dict[str, Any]] = []
    for b in backlinks:
        is_act = bool(b.get("stato_attivo"))
        rel_str = str(b.get("rel_attribute") or "").strip().lower()
        src_str = str(b.get("url_sorgente") or "").lower()
        anc_str = str(b.get("anchor_text") or "").lower()

        if filtro_stato == "Attivi" and not is_act:
            continue
        if filtro_stato == "Non Attivi" and is_act:
            continue
        if filtro_rel != "Tutti" and filtro_rel.lower() not in rel_str:
            continue
        if filtro_search.strip():
            q = filtro_search.strip().lower()
            if q not in src_str and q not in anc_str:
                continue

        filtered_bl.append(b)

    st.divider()

    if not filtered_bl:
        st.info(
            "Nessun backlink trovato con i filtri selezionati.",
            icon=":material/info:",
        )
    else:
        h_cols = st.columns([1.15, 0.7, 0.95, 2.3, 2.5, 1.35], vertical_alignment="center")
        h_cols[0].caption("**Stato**")
        h_cols[1].caption("**HTTP**")
        h_cols[2].caption("**Rel**")
        h_cols[3].caption("**URL Sorgente**")
        h_cols[4].caption("**URL Destinazione & Anchor Text**")
        h_cols[5].caption("**Azioni**")

        for bl in filtered_bl:
            bl_id = str(bl["id"])
            src_url = (bl.get("url_sorgente") or "").strip()
            src_dom = clean_domain(src_url) or src_url
            dest_url = (bl.get("url_destinazione") or "").strip()
            anchor_txt = (bl.get("anchor_text") or "").strip()
            has_checked = bool(bl.get("ultimo_check"))

            with st.container(border=True):
                rc = st.columns([1.15, 0.7, 0.95, 2.3, 2.5, 1.35], vertical_alignment="center")

                # 1. Stato (Badge circolare/pillola VERDE o ROSSO)
                with rc[0]:
                    st.markdown(
                        _render_status_pill_html(bl.get("stato_attivo"), checked=has_checked),
                        unsafe_allow_html=True,
                    )
                    if has_checked and not bool(bl.get("stato_attivo")):
                        target_dom = (
                            clean_domain(dest_url)
                            or mio_dominio_pulito
                            or "mondoirrigazione.it"
                        )
                        raw_http = bl.get("http_status")
                        try:
                            http_int = int(raw_http) if raw_http is not None and not pd.isna(raw_http) else 0
                        except (ValueError, TypeError):
                            http_int = 0

                        if http_int == 0:
                            reason_txt = "Connessione fallita / Timeout (HTTP 0)"
                        elif http_int != 200:
                            reason_txt = f"HTTP {http_int} (blocco o pagina non OK)"
                        else:
                            reason_txt = f"Pagina 200 OK ma nessun link o menzione verso {target_dom} trovato"
                        st.caption(f":material/info: {reason_txt}")

                # 2. HTTP Status
                with rc[1]:
                    st.markdown(
                        _render_http_badge_html(bl.get("http_status")),
                        unsafe_allow_html=True,
                    )

                # 3. Rel Badge
                with rc[2]:
                    st.markdown(
                        _render_rel_badge_html(bl.get("rel_attribute")),
                        unsafe_allow_html=True,
                    )

                # 4. URL Sorgente
                with rc[3]:
                    st.markdown(
                        f"**`{src_dom}`** [:material/open_in_new:]({src_url})"
                    )
                    st.caption(
                        f"{src_url[:52] + ('...' if len(src_url) > 52 else '')} • {_format_check_ts(bl.get('ultimo_check'))}"
                    )

                # 5. URL Destinazione & Anchor Text / Menzione
                with rc[4]:
                    is_menzione_row = "menzione" in str(bl.get("rel_attribute") or "").lower()
                    if is_menzione_row:
                        st.caption(":material/record_voice_over: **Menzione testuale del brand (senza link `<a>`)**")
                        if anchor_txt:
                            st.caption(f"**{anchor_txt}**")
                    else:
                        if dest_url:
                            dest_short = dest_url[:48] + ("..." if len(dest_url) > 48 else "")
                            st.markdown(f"[{dest_short}]({dest_url}) [:material/open_in_new:]({dest_url})")
                        else:
                            st.caption("Qualsiasi pagina del nostro dominio")

                        if anchor_txt:
                            st.caption(f'Anchor: **"{anchor_txt}"**')
                        else:
                            st.caption("Anchor: *Non rilevato*")

                # 6. Azioni sulla riga: Refresh singolo, Modifica, Elimina
                with rc[5]:
                    ac1, ac2, ac3 = st.columns(3)
                    with ac1:
                        if st.button(
                            "",
                            icon=":material/refresh:",
                            help="Ricontrolla subito questo backlink",
                            key=f"btn_refresh_bl_{bl_id}",
                            use_container_width=True,
                        ):
                            try:
                                esito = verifica_singolo_backlink(
                                    src_url,
                                    dest_url,
                                    mio_sito_web,
                                    nome_azienda=mio_nome_azienda,
                                )
                                salva_esito_backlink(
                                    backlink_id=bl_id,
                                    stato_attivo=bool(esito.get("stato_attivo")),
                                    http_status=esito.get("http_status"),
                                    rel_attribute=esito.get("rel_attribute"),
                                    anchor_text=esito.get("anchor_text"),
                                    url_destinazione_trovato=esito.get("url_destinazione_trovato"),
                                )
                                st.toast(
                                    f"Backlink verificato: {'Attivo' if esito.get('stato_attivo') else 'Non attivo'} (HTTP {esito.get('http_status')})",
                                    icon=":material/check_circle:",
                                )
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Errore verifica: {exc}", icon=":material/error:")
                    with ac2:
                        if st.button(
                            "",
                            icon=":material/edit:",
                            help="Modifica URL backlink",
                            key=f"btn_edit_bl_{bl_id}",
                            use_container_width=True,
                        ):
                            dialog_modifica_backlink(
                                bl, mio_sito_web, nome_azienda=mio_nome_azienda
                            )
                    with ac3:
                        if st.button(
                            "",
                            icon=":material/delete:",
                            help="Elimina backlink",
                            key=f"btn_del_bl_{bl_id}",
                            use_container_width=True,
                        ):
                            dialog_elimina_backlink(bl)


# -------------------------------------------------------------------------
# TAB 2: GESTIONE LINK (AGGIUNGI / CSV)
# -------------------------------------------------------------------------
with tab_manage_bl:
    col_add_bl, col_csv_bl = st.columns(2)

    # 1. Aggiungi Link Manuale
    with col_add_bl:
        with st.container(border=True):
            st.markdown("#### :material/add_link: Aggiungi Link Manuale")
            st.caption(
                "Se lasci vuoto l'URL Destinazione, il sistema verificherà che la pagina sorgente contenga almeno un link verso qualsiasi pagina del nostro dominio."
            )
            with st.form("form_add_single_backlink", clear_on_submit=True):
                in_url_sorgente = st.text_input(
                    "URL Sorgente * (articolo/pagina esterna che ci linka)",
                    placeholder="https://www.portale-giardinaggio.it/articolo-irrigazione",
                )
                in_url_dest = st.text_input(
                    "URL Destinazione sul nostro sito (Opzionale)",
                    placeholder=mio_sito_web or "https://www.mondoirrigazione.it/...",
                )
                verify_immediately = st.checkbox(
                    "Verifica subito dopo l'inserimento",
                    value=True,
                )
                sub_add_bl = st.form_submit_button(
                    "Aggiungi Backlink",
                    type="primary",
                    icon=":material/save:",
                    use_container_width=True,
                )

                if sub_add_bl:
                    if not in_url_sorgente.strip():
                        st.warning(
                            "Inserisci l'URL Sorgente della pagina esterna.",
                            icon=":material/warning:",
                        )
                    else:
                        try:
                            inserted = add_backlink(
                                url_sorgente=in_url_sorgente.strip(),
                                url_destinazione=in_url_dest.strip() or None,
                            )
                            if verify_immediately and inserted and inserted.get("id"):
                                chk = verifica_singolo_backlink(
                                    inserted.get("url_sorgente") or in_url_sorgente.strip(),
                                    inserted.get("url_destinazione") or in_url_dest.strip(),
                                    mio_sito_web,
                                    nome_azienda=mio_nome_azienda,
                                )
                                salva_esito_backlink(
                                    backlink_id=str(inserted["id"]),
                                    stato_attivo=bool(chk.get("stato_attivo")),
                                    http_status=chk.get("http_status"),
                                    rel_attribute=chk.get("rel_attribute"),
                                    anchor_text=chk.get("anchor_text"),
                                    url_destinazione_trovato=chk.get("url_destinazione_trovato"),
                                )
                            st.toast(
                                "Backlink aggiunto con successo!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Errore durante il salvataggio del backlink: {exc}",
                                icon=":material/error:",
                            )

    # 2. Carica Backlink da CSV
    with col_csv_bl:
        with st.container(border=True):
            st.markdown("#### :material/upload_file: Carica Backlink da CSV")
            st.caption(
                "Supporta automaticamente virgola (`,`) e punto e virgola (`;`). "
                "Colonne: `url_sorgente` (obbligatoria), `url_destinazione` (opzionale)."
            )
            template_bl_csv = (
                "url_sorgente,url_destinazione\n"
                f"https://www.esempio-blog.it/guida-irrigazione,{mio_sito_web or 'https://www.mondoirrigazione.it'}\n"
                "https://www.portale-verde.it/migliori-centraline,\n"
            )
            st.download_button(
                label="Scarica template CSV Backlink",
                data=template_bl_csv.encode("utf-8"),
                file_name="template_backlinks.csv",
                mime="text/csv",
                icon=":material/download:",
                use_container_width=True,
            )

            uploaded_bl_csv = st.file_uploader(
                "Seleziona file CSV Backlink",
                type=["csv"],
                key="uploader_backlinks_csv",
            )
            if uploaded_bl_csv is not None:
                if st.button(
                    "Importa Backlink dal CSV",
                    type="primary",
                    icon=":material/cloud_upload:",
                    use_container_width=True,
                    key="btn_import_backlinks_csv",
                ):
                    try:
                        df_bl = read_csv_flexible(uploaded_bl_csv)
                        if "url_sorgente" not in df_bl.columns:
                            st.error(
                                "Il CSV deve contenere la colonna obbligatoria `url_sorgente`.",
                                icon=":material/error:",
                            )
                        else:
                            recs: List[Dict[str, Any]] = []
                            for _, r in df_bl.iterrows():
                                raw_src = r.get("url_sorgente")
                                if raw_src is None or pd.isna(raw_src) or not str(raw_src).strip():
                                    continue
                                raw_dest = (
                                    r.get("url_destinazione")
                                    if "url_destinazione" in df_bl.columns
                                    else None
                                )
                                recs.append(
                                    {
                                        "url_sorgente": str(raw_src).strip(),
                                        "url_destinazione": (
                                            None
                                            if raw_dest is None
                                            or pd.isna(raw_dest)
                                            or not str(raw_dest).strip()
                                            else str(raw_dest).strip()
                                        ),
                                    }
                                )
                            n_ins = bulk_insert_backlinks(recs)
                            st.toast(
                                f"Importati/aggiornati {n_ins} backlink!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                    except Exception as exc:
                        st.error(
                            f"Errore durante l'importazione CSV: {exc}",
                            icon=":material/error:",
                        )
