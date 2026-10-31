import io
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from db import (
    add_competitor_prezzi,
    bulk_upsert_prodotti,
    delete_competitor,
    delete_prodotto,
    get_competitors_prezzi,
    get_prodotti,
    get_prodotti_competitor,
    read_csv_flexible,
    scollega_prodotto_competitor,
    update_competitor,
    update_prodotto,
    upsert_prodotti_competitor,
    upsert_prodotto,
)
from scraper import esegui_auto_discovery_e_scraping, estrai_prezzo_da_url

st.header(":material/sell: Prezzi & Competitor")
st.caption("Analisi posizionamento prezzi per categoria e marchio, gestione catalogo e link competitor.")

# Caricamento dati base
prodotti = get_prodotti()
competitors = get_competitors_prezzi(only_active=True)
associazioni = get_prodotti_competitor()

# Mappa rapida: (prodotto_id_str, competitor_id_str) -> record associazione
assoc_map: Dict[tuple, Dict[str, Any]] = {
    (str(a["prodotto_id"]), str(a["competitor_id"])): a for a in associazioni
}


# =========================================================================
# DIALOG POPUP PER MODIFICA ED ELIMINAZIONE PRODOTTI, COMPETITOR E LINK
# =========================================================================


@st.dialog("Modifica Prodotto")
def dialog_modifica_prodotto(prodotto: Dict[str, Any]) -> None:
    """Popup per modificare i dati di un prodotto esistente."""
    prod_id = str(prodotto["id"])
    with st.form(f"form_edit_prod_{prod_id}"):
        edit_sku = st.text_input(
            "Codice / SKU (Opzionale)",
            value=str(prodotto.get("sku") or ""),
            placeholder="Lascia vuoto se non presente",
        )
        edit_descrizione = st.text_input(
            "Descrizione *", value=str(prodotto.get("descrizione") or "")
        )
        c1, c2 = st.columns(2)
        with c1:
            edit_categoria = st.text_input(
                "Categoria", value=str(prodotto.get("categoria") or "")
            )
        with c2:
            edit_marchio = st.text_input(
                "Marchio", value=str(prodotto.get("marchio") or "")
            )
        edit_prezzo = st.number_input(
            "Mio Prezzo (€) *",
            min_value=0.0,
            step=0.5,
            format="%.2f",
            value=float(prodotto.get("mio_prezzo") or 0.0),
        )
        edit_mio_url = st.text_input(
            "URL Mio Prodotto (Opzionale - se vuoto lo cerca l'AI)",
            value=str(prodotto.get("mio_url") or ""),
            placeholder="https://www.miosito.it/prodotto...",
        )

        salva_btn = st.form_submit_button(
            "Salva Modifiche",
            icon=":material/save:",
            type="primary",
            use_container_width=True,
        )

        if salva_btn:
            if not edit_descrizione.strip():
                st.warning(
                    "Il campo Descrizione è obbligatorio.",
                    icon=":material/warning:",
                )
            else:
                try:
                    sku_val = edit_sku.strip() if edit_sku and edit_sku.strip() else None
                    url_val = (
                        edit_mio_url.strip() if edit_mio_url and edit_mio_url.strip() else None
                    )
                    update_prodotto(
                        prod_id,
                        {
                            "sku": sku_val,
                            "descrizione": edit_descrizione.strip(),
                            "categoria": edit_categoria.strip() or "Generale",
                            "marchio": edit_marchio.strip() or "Generico",
                            "mio_prezzo": round(float(edit_prezzo), 2),
                            "mio_url": url_val,
                        },
                    )
                    st.toast(
                        f"Prodotto '{edit_descrizione.strip()}' aggiornato!",
                        icon=":material/check_circle:",
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(
                        f"Errore durante l'aggiornamento: {exc}",
                        icon=":material/error:",
                    )


@st.dialog("Conferma Eliminazione")
def dialog_elimina_prodotto(prodotto: Dict[str, Any]) -> None:
    """Popup di conferma per l'eliminazione di un singolo prodotto."""
    prod_id = str(prodotto["id"])
    sku_val = prodotto.get("sku") or "—"
    desc_val = prodotto.get("descrizione", "")

    st.write(f"Sei sicuro di voler eliminare definitivamente il prodotto **{desc_val}**?")
    st.caption(f"Codice / SKU: {sku_val}")

    col_annulla, col_conferma = st.columns(2)
    with col_annulla:
        if st.button("Annulla", icon=":material/close:", use_container_width=True):
            st.rerun()
    with col_conferma:
        if st.button(
            "Conferma Elimina",
            icon=":material/delete:",
            type="primary",
            use_container_width=True,
        ):
            try:
                delete_prodotto(prod_id)
                st.toast(f"Prodotto '{desc_val}' eliminato.", icon=":material/delete:")
                st.rerun()
            except Exception as exc:
                st.error(f"Errore durante l'eliminazione: {exc}", icon=":material/error:")


@st.dialog("Modifica Competitor")
def dialog_modifica_competitor(comp: Dict[str, Any]) -> None:
    """Popup per modificare nome e dominio di un competitor."""
    comp_id = str(comp["id"])
    with st.form(f"form_edit_comp_{comp_id}"):
        edit_nome = st.text_input("Nome Competitor *", value=str(comp.get("nome") or ""))
        edit_dominio = st.text_input(
            "Dominio / Sito Web *", value=str(comp.get("dominio") or "")
        )
        salva_c = st.form_submit_button(
            "Salva Modifiche",
            icon=":material/save:",
            type="primary",
            use_container_width=True,
        )
        if salva_c:
            if not edit_nome.strip() or not edit_dominio.strip():
                st.warning(
                    "Inserisci sia il nome che il dominio.",
                    icon=":material/warning:",
                )
            else:
                try:
                    update_competitor(
                        comp_id,
                        {
                            "nome": edit_nome.strip(),
                            "dominio": edit_dominio.strip(),
                        },
                    )
                    st.toast(
                        f"Competitor '{edit_nome.strip()}' aggiornato!",
                        icon=":material/check_circle:",
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Errore durante il salvataggio: {exc}", icon=":material/error:")


@st.dialog("Conferma Eliminazione Competitor")
def dialog_elimina_competitor(comp: Dict[str, Any]) -> None:
    """Popup di conferma per eliminare un competitor."""
    comp_id = str(comp["id"])
    comp_nome = comp.get("nome", "")
    st.write(f"Vuoi eliminare il competitor **{comp_nome}** ({comp.get('dominio', '')})?")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Annulla", icon=":material/close:", use_container_width=True):
            st.rerun()
    with col_b:
        if st.button(
            "Conferma Elimina",
            icon=":material/delete:",
            type="primary",
            use_container_width=True,
        ):
            try:
                delete_competitor(comp_id)
                st.toast(f"Competitor '{comp_nome}' eliminato.", icon=":material/delete:")
                st.rerun()
            except Exception as exc:
                st.error(f"Errore durante l'eliminazione: {exc}", icon=":material/error:")


@st.dialog("Modifica Link e Prezzo Competitor")
def dialog_modifica_link_competitor(
    prodotto: Dict[str, Any],
    comp: Dict[str, Any],
    rec: Dict[str, Any],
) -> None:
    """
    Popup per modificare l'URL e il prezzo di un prodotto per lo specifico competitor,
    con possibilità di rileggere il prezzo in tempo reale dal nuovo link oppure salvare manualmente.
    """
    p_id = str(prodotto["id"])
    c_id = str(comp["id"])
    desc = prodotto.get("descrizione") or ""
    sku_val = prodotto.get("sku") or "—"
    mio_p = float(prodotto.get("mio_prezzo") or 0.0)
    comp_nome = comp.get("nome") or "Competitor"

    old_url = str(rec.get("url_prodotto") or "")
    old_price = (
        round(float(rec["ultimo_prezzo"]), 2)
        if rec.get("ultimo_prezzo") is not None
        else None
    )
    old_prev = (
        round(float(rec["prezzo_precedente"]), 2)
        if rec.get("prezzo_precedente") is not None
        else None
    )

    st.markdown(f"**Prodotto:** {desc}")
    st.caption(
        f"SKU: `{sku_val}` • Mio Prezzo: **€ {mio_p:.2f}** • Competitor: **{comp_nome}**"
    )

    nuovo_url = st.text_input(
        "URL Prodotto Competitor",
        value=old_url,
        placeholder="https://...",
        key=f"dlg_url_{p_id}_{c_id}",
    )
    prezzo_manuale = st.number_input(
        "Prezzo Competitor (€)",
        min_value=0.0,
        step=0.5,
        format="%.2f",
        value=float(old_price if old_price is not None else 0.0),
        help="Lascia 0.00 se non disponibile oppure usa il pulsante di rilettura automatica dal sito.",
        key=f"dlg_price_{p_id}_{c_id}",
    )

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    # Pulsante 1: Salva URL e Rileggi Prezzo dal sito
    if st.button(
        "Salva URL e Rileggi Prezzo dal sito",
        icon=":material/sync:",
        type="primary",
        use_container_width=True,
        key=f"btn_sync_url_{p_id}_{c_id}",
    ):
        clean_u = nuovo_url.strip() or None
        if clean_u is None:
            try:
                scollega_prodotto_competitor(p_id, c_id)
                st.toast(
                    "URL rimosso e prezzo competitor azzerato!",
                    icon=":material/check_circle:",
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Errore durante l'azzeramento: {exc}", icon=":material/error:")
        else:
            scraped_p = None
            with st.spinner("Lettura prezzo dalla pagina del competitor..."):
                scraped_p = estrai_prezzo_da_url(
                    clean_u,
                    desc,
                    mio_prezzo_riferimento=mio_p if mio_p > 0 else None,
                )

            final_price = (
                round(float(scraped_p), 2)
                if scraped_p is not None and scraped_p > 0
                else (round(float(prezzo_manuale), 2) if prezzo_manuale > 0 else None)
            )
            prev_to_save = (
                old_price
                if (final_price != old_price and old_price is not None)
                else old_prev
            )

            try:
                upsert_prodotti_competitor(
                    [
                        {
                            "prodotto_id": p_id,
                            "competitor_id": c_id,
                            "url_prodotto": clean_u,
                            "ultimo_prezzo": final_price,
                            "prezzo_precedente": prev_to_save,
                        }
                    ],
                    record_history=True,
                )
                if scraped_p is not None and scraped_p > 0:
                    st.toast(
                        f"URL salvato e prezzo rilevato dal sito: € {scraped_p:.2f}",
                        icon=":material/check_circle:",
                    )
                else:
                    st.toast(
                        "URL salvato (prezzo automatico non esposto sulla pagina).",
                        icon=":material/info:",
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"Errore durante il salvataggio: {exc}", icon=":material/error:")

    # Pulsante 2: Salva manualmente (se URL è vuoto, azzera automaticamente anche ultimo_prezzo e prezzo_precedente)
    if st.button(
        "Salva manualmente",
        icon=":material/save:",
        use_container_width=True,
        key=f"btn_manual_save_{p_id}_{c_id}",
    ):
        clean_u = nuovo_url.strip() or None
        if clean_u is None:
            final_price = None
            prev_to_save = None
        else:
            final_price = round(float(prezzo_manuale), 2) if prezzo_manuale > 0 else None
            prev_to_save = (
                old_price
                if (final_price != old_price and old_price is not None)
                else old_prev
            )
        try:
            upsert_prodotti_competitor(
                [
                    {
                        "prodotto_id": p_id,
                        "competitor_id": c_id,
                        "url_prodotto": clean_u,
                        "ultimo_prezzo": final_price,
                        "prezzo_precedente": prev_to_save,
                    }
                ],
                record_history=True,
            )
            if clean_u is None:
                st.toast(
                    "URL svuotato: link e prezzo competitor azzerati!",
                    icon=":material/check_circle:",
                )
            else:
                st.toast("Link e prezzo salvati manualmente!", icon=":material/check_circle:")
            st.rerun()
        except Exception as exc:
            st.error(f"Errore durante il salvataggio: {exc}", icon=":material/error:")

    # Pulsante 3: Scollega URL e Azzera Prezzo con un solo clic
    if st.button(
        "Scollega URL e Azzera Prezzo",
        icon=":material/link_off:",
        type="secondary",
        use_container_width=True,
        key=f"btn_unlink_clear_{p_id}_{c_id}",
    ):
        try:
            scollega_prodotto_competitor(p_id, c_id)
            st.toast(
                "URL scollegato e prezzo azzerato per questo competitor!",
                icon=":material/delete:",
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Errore durante lo scollegamento: {exc}", icon=":material/error:")


# =========================================================================
# FUNZIONI DI SUPPORTO PER CALCOLO E RENDERING REPORT
# =========================================================================


def calcola_diff_pct(mio_prezzo: Optional[float], comp_prezzo: Optional[float]) -> Optional[float]:
    """Calcola ((ultimo_prezzo - mio_prezzo) / mio_prezzo) * 100."""
    try:
        if mio_prezzo is None or comp_prezzo is None:
            return None
        mio_val = float(mio_prezzo)
        comp_val = float(comp_prezzo)
        if mio_val <= 0 or comp_val <= 0:
            return None
        return ((comp_val - mio_val) / mio_val) * 100.0
    except (ValueError, TypeError):
        return None


def badge_diff_html(diff_val: Optional[float]) -> str:
    """Restituisce un badge HTML minimale con colore semantico per la differenza %."""
    if diff_val is None or pd.isna(diff_val):
        return '<span style="color:#94A3B8; font-size:0.88rem;">N/D</span>'
    segno = "+" if diff_val > 0 else ""
    if diff_val < 0:
        # Competitor più basso del nostro -> Arancio/Rosso
        return (
            f'<span style="color:#F87171; background:rgba(248,113,113,0.14); '
            f'padding:2px 8px; border-radius:6px; font-weight:600; font-size:0.88rem;">'
            f"{segno}{diff_val:.1f}%</span>"
        )
    # Competitor uguale o più alto -> Verde
    return (
        f'<span style="color:#4ADE80; background:rgba(74,222,128,0.14); '
        f'padding:2px 8px; border-radius:6px; font-weight:600; font-size:0.88rem;">'
        f"{segno}{diff_val:.1f}%</span>"
    )


def format_evoluzione_text(
    ultimo_prezzo: Optional[float],
    prezzo_precedente: Optional[float],
) -> str:
    """
    Confronta `ultimo_prezzo` con `prezzo_precedente`:
    - Aumento: `▲ +€ X.XX (+Y.Y%)`
    - Diminuzione: `▼ -€ X.XX (-Y.Y%)`
    - Uguale o `prezzo_precedente` None: `= Stabile`
    """
    try:
        if ultimo_prezzo is None or pd.isna(ultimo_prezzo) or float(ultimo_prezzo) <= 0:
            return "N/D"
        curr = round(float(ultimo_prezzo), 2)
        if (
            prezzo_precedente is None
            or pd.isna(prezzo_precedente)
            or float(prezzo_precedente) <= 0
        ):
            return "= Stabile"
        prev = round(float(prezzo_precedente), 2)
        delta = round(curr - prev, 2)
        if abs(delta) < 0.01:
            return "= Stabile"
        pct = ((curr - prev) / prev) * 100.0
        if delta > 0:
            return f"▲ +€ {delta:.2f} (+{pct:.1f}%)"
        return f"▼ -€ {abs(delta):.2f} ({pct:.1f}%)"
    except (ValueError, TypeError):
        return "= Stabile"


def badge_evoluzione_html(
    ultimo_prezzo: Optional[float],
    prezzo_precedente: Optional[float],
) -> str:
    """Restituisce un badge HTML colorato per l'evoluzione rispetto alla rilevazione precedente."""
    txt = format_evoluzione_text(ultimo_prezzo, prezzo_precedente)
    if txt == "N/D":
        return '<span style="color:#94A3B8; font-size:0.84rem;">N/D</span>'
    if txt.startswith("▲"):
        return (
            f'<span style="color:#FB923C; background:rgba(251,146,60,0.15); '
            f'padding:2px 8px; border-radius:6px; font-weight:600; font-size:0.82rem;">'
            f"{txt}</span>"
        )
    if txt.startswith("▼"):
        return (
            f'<span style="color:#38BDF8; background:rgba(56,189,248,0.15); '
            f'padding:2px 8px; border-radius:6px; font-weight:600; font-size:0.82rem;">'
            f"{txt}</span>"
        )
    return (
        '<span style="color:#94A3B8; background:rgba(148,163,184,0.12); '
        'padding:2px 8px; border-radius:6px; font-weight:600; font-size:0.82rem;">= Stabile</span>'
    )


def badge_evoluzione_inline_short_html(
    ultimo_prezzo: Optional[float],
    prezzo_precedente: Optional[float],
) -> str:
    """
    Restituisce un badge compatto da affiancare direttamente ad ogni badge di Scostamento %:
    - `▲ +€ X.XX (+Y.Y%)` (rosso/arancio) se il prezzo competitor è salito rispetto a `prezzo_precedente`
    - `▼ -€ X.XX (-Y.Y%)` (verde/blu) se il prezzo competitor è sceso rispetto a `prezzo_precedente`
    - `=` (grigio) se invariato
    """
    try:
        if ultimo_prezzo is None or pd.isna(ultimo_prezzo) or float(ultimo_prezzo) <= 0:
            return ""
        curr = round(float(ultimo_prezzo), 2)
        if (
            prezzo_precedente is None
            or pd.isna(prezzo_precedente)
            or float(prezzo_precedente) <= 0
        ):
            return (
                '<span title="Invariato rispetto alla rilevazione precedente" '
                'style="color:#94A3B8; background:rgba(148,163,184,0.12); '
                'padding:2px 6px; border-radius:5px; font-weight:600; font-size:0.78rem; margin-left:5px;">=</span>'
            )
        prev = round(float(prezzo_precedente), 2)
        delta = round(curr - prev, 2)
        if abs(delta) < 0.01:
            return (
                '<span title="Invariato rispetto alla rilevazione precedente" '
                'style="color:#94A3B8; background:rgba(148,163,184,0.12); '
                'padding:2px 6px; border-radius:5px; font-weight:600; font-size:0.78rem; margin-left:5px;">=</span>'
            )
        pct = ((curr - prev) / prev) * 100.0
        if delta > 0:
            return (
                f'<span title="Prezzo competitor aumentato rispetto alla rilevazione precedente" '
                f'style="color:#FB923C; background:rgba(251,146,60,0.15); '
                f'padding:2px 6px; border-radius:5px; font-weight:600; font-size:0.78rem; margin-left:5px;">'
                f"▲ +€ {delta:.2f} (+{pct:.1f}%)</span>"
            )
        return (
            f'<span title="Prezzo competitor diminuito rispetto alla rilevazione precedente" '
            f'style="color:#38BDF8; background:rgba(56,189,248,0.15); '
            f'padding:2px 6px; border-radius:5px; font-weight:600; font-size:0.78rem; margin-left:5px;">'
            f"▼ -€ {abs(delta):.2f} ({pct:.1f}%)</span>"
        )
    except (ValueError, TypeError):
        return ""


def filtra_e_ordina_prodotti(
    prodotti_list: List[Dict[str, Any]],
    search_query: str = "",
    cat_filter: str = "Tutte",
    mar_filter: str = "Tutti",
    sort_by: str = "Nome (Descrizione A-Z)",
) -> List[Dict[str, Any]]:
    """Filtra e ordina la lista dei prodotti in base a ricerca testuale, Categoria, Marchio e criterio scelto."""
    q = (search_query or "").strip().lower()
    result = []
    for p in prodotti_list:
        cat_val = (p.get("categoria") or "Generale").strip() or "Generale"
        mar_val = (p.get("marchio") or "Generico").strip() or "Generico"
        if cat_filter and cat_filter != "Tutte" and cat_val != cat_filter:
            continue
        if mar_filter and mar_filter != "Tutti" and mar_val != mar_filter:
            continue
        if q:
            desc_l = (p.get("descrizione") or "").lower()
            sku_l = (p.get("sku") or "").lower()
            if q not in desc_l and q not in sku_l and q not in cat_val.lower() and q not in mar_val.lower():
                continue
        result.append(p)

    if sort_by == "Nome (Z-A)":
        result.sort(key=lambda x: (x.get("descrizione") or "").lower(), reverse=True)
    elif sort_by == "Categoria":
        result.sort(
            key=lambda x: (
                (x.get("categoria") or "Generale").lower(),
                (x.get("descrizione") or "").lower(),
            )
        )
    elif sort_by == "Marchio":
        result.sort(
            key=lambda x: (
                (x.get("marchio") or "Generico").lower(),
                (x.get("descrizione") or "").lower(),
            )
        )
    elif sort_by == "SKU":
        result.sort(
            key=lambda x: (
                0 if (x.get("sku") or "").strip() else 1,
                (x.get("sku") or "").lower(),
                (x.get("descrizione") or "").lower(),
            )
        )
    elif sort_by == "Prezzo (Crescente)":
        result.sort(
            key=lambda x: (
                float(x.get("mio_prezzo") or 0.0),
                (x.get("descrizione") or "").lower(),
            )
        )
    elif sort_by == "Prezzo (Decrescente)":
        result.sort(
            key=lambda x: (
                -float(x.get("mio_prezzo") or 0.0),
                (x.get("descrizione") or "").lower(),
            )
        )
    else:
        # Default: Nome (Descrizione A-Z)
        result.sort(key=lambda x: (x.get("descrizione") or "").lower())

    return result


def render_summary_grid(
    group_type: str,
    group_label: str,
    prodotti_list: List[Dict[str, Any]],
    competitors_list: List[Dict[str, Any]],
) -> None:
    """Renderizza la griglia a 3 colonne dei box di sintesi per Categoria o Marchio con Scostamento % ed Evoluzione."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in prodotti_list:
        val = (p.get(group_type) or "Non specificato").strip() or "Non specificato"
        groups.setdefault(val, []).append(p)

    if not groups:
        st.info(f"Nessun dato disponibile per {group_label.lower()}.", icon=":material/info:")
        return

    sorted_keys = sorted(groups.keys(), key=lambda x: x.lower())
    cols = st.columns(3)

    for idx, group_name in enumerate(sorted_keys):
        group_prods = groups[group_name]
        col = cols[idx % 3]

        with col:
            with st.container(border=True):
                st.markdown(f"#### {group_name}")
                st.caption(
                    f"{len(group_prods)} prodott{'o' if len(group_prods) == 1 else 'i'} a catalogo"
                )

                if not competitors_list:
                    st.caption("Nessun competitor attivo configurato.")
                else:
                    rows_html = []
                    for comp in competitors_list:
                        c_id = str(comp["id"])
                        c_nome = comp["nome"]
                        diffs = []
                        curr_prices_for_evol = []
                        prev_prices_for_evol = []
                        for p in group_prods:
                            p_id = str(p["id"])
                            mio_p = p.get("mio_prezzo")
                            rec = assoc_map.get((p_id, c_id))
                            comp_p = rec.get("ultimo_prezzo") if rec else None
                            comp_prev_p = rec.get("prezzo_precedente") if rec else None
                            d = calcola_diff_pct(mio_p, comp_p)
                            if d is not None:
                                diffs.append(d)
                            if comp_p is not None and float(comp_p) > 0:
                                c_val = float(comp_p)
                                p_val = (
                                    float(comp_prev_p)
                                    if (comp_prev_p is not None and float(comp_prev_p) > 0)
                                    else c_val
                                )
                                curr_prices_for_evol.append(c_val)
                                prev_prices_for_evol.append(p_val)

                        avg_diff = sum(diffs) / len(diffs) if diffs else None
                        avg_curr = (
                            sum(curr_prices_for_evol) / len(curr_prices_for_evol)
                            if curr_prices_for_evol
                            else None
                        )
                        avg_prev = (
                            sum(prev_prices_for_evol) / len(prev_prices_for_evol)
                            if prev_prices_for_evol
                            else None
                        )
                        badge = badge_diff_html(avg_diff)
                        evol_inline = (
                            badge_evoluzione_inline_short_html(avg_curr, avg_prev)
                            if avg_diff is not None
                            else ""
                        )
                        rows_html.append(
                            f'<div style="display:flex; justify-content:space-between; '
                            f'align-items:center; padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.06);">'
                            f'<span style="color:#E2E8F0; font-size:0.9rem;">{c_nome}</span>'
                            f'<span style="display:inline-flex; align-items:center;">{badge}{evol_inline}</span></div>'
                        )

                    st.markdown("".join(rows_html), unsafe_allow_html=True)

                st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
                _, btn_col = st.columns([1.2, 1])
                with btn_col:
                    if st.button(
                        "Dettaglio",
                        icon=":material/arrow_forward:",
                        key=f"btn_det_{group_type}_{group_name}",
                        use_container_width=True,
                    ):
                        st.session_state["prezzi_dettaglio_tipo"] = group_type
                        st.session_state["prezzi_dettaglio_valore"] = group_name
                        st.rerun()


def render_detail_table(
    tipo_filtro: str,
    valore_filtro: str,
    prodotti_list: List[Dict[str, Any]],
    competitors_list: List[Dict[str, Any]],
) -> None:
    """Mostra la vista di dettaglio dei prodotti appartenenti alla Categoria o al Marchio selezionato."""
    etichetta_tipo = "Categoria" if tipo_filtro == "categoria" else "Marchio"

    with st.container(border=True):
        top_left, top_right = st.columns([4, 1], vertical_alignment="center")
        with top_left:
            st.subheader(f":material/filter_list: Dettaglio {etichetta_tipo}: {valore_filtro}")
        with top_right:
            if st.button(
                "Mostrare tutti",
                icon=":material/close:",
                type="secondary",
                use_container_width=True,
                key="btn_reset_dettaglio_prezzi",
            ):
                st.session_state.pop("prezzi_dettaglio_tipo", None)
                st.session_state.pop("prezzi_dettaglio_valore", None)
                st.rerun()

        filtrati = [
            p
            for p in prodotti_list
            if ((p.get(tipo_filtro) or "Non specificato").strip() or "Non specificato")
            == valore_filtro
        ]

        if not filtrati:
            st.info("Nessun prodotto trovato per questo filtro.", icon=":material/info:")
            return

        rows = []
        diff_columns = []
        evol_columns = []

        for comp in competitors_list:
            diff_columns.append(f"{comp['nome']} Diff %")
            evol_columns.append(f"{comp['nome']} Evoluzione (vs prec.)")

        for p in filtrati:
            p_id = str(p["id"])
            mio_p = float(p.get("mio_prezzo") or 0.0)
            row: Dict[str, Any] = {
                "SKU": p.get("sku") or "—",
                "Descrizione": p.get("descrizione", ""),
                "Mio Prezzo (€)": mio_p,
            }
            for comp in competitors_list:
                c_id = str(comp["id"])
                c_nome = comp["nome"]
                rec = assoc_map.get((p_id, c_id))
                comp_p = (
                    float(rec["ultimo_prezzo"])
                    if rec and rec.get("ultimo_prezzo") is not None
                    else None
                )
                comp_prev_p = (
                    float(rec["prezzo_precedente"])
                    if rec and rec.get("prezzo_precedente") is not None
                    else None
                )
                diff_val = calcola_diff_pct(mio_p, comp_p)
                evol_txt = format_evoluzione_text(comp_p, comp_prev_p)
                if diff_val is not None:
                    evol_sym = (
                        "▲"
                        if evol_txt.startswith("▲")
                        else ("▼" if evol_txt.startswith("▼") else "=")
                    )
                    diff_display = f"{'+' if diff_val > 0 else ''}{diff_val:.1f}%  {evol_sym}"
                else:
                    diff_display = "N/D"
                row[f"{c_nome} (€)"] = comp_p
                row[f"{c_nome} Diff %"] = diff_display
                row[f"{c_nome} Evoluzione (vs prec.)"] = evol_txt

            rows.append(row)

        df_det = pd.DataFrame(rows)

        format_dict: Dict[str, Any] = {
            "Mio Prezzo (€)": "€ {:.2f}",
        }
        for comp in competitors_list:
            c_nome = comp["nome"]
            format_dict[f"{c_nome} (€)"] = lambda x: f"€ {x:.2f}" if pd.notna(x) else "N/D"

        def style_diff_cell(val: Any) -> str:
            s = str(val or "").strip()
            if not s or s == "N/D":
                return "color: #94A3B8;"
            if s.startswith("-"):
                return "color: #F87171; background-color: rgba(248, 113, 113, 0.14); font-weight: 600;"
            return "color: #4ADE80; background-color: rgba(74, 222, 128, 0.14); font-weight: 600;"

        def style_evol_cell(val: Any) -> str:
            s = str(val or "")
            if s.startswith("▲"):
                return "color: #FB923C; background-color: rgba(251, 146, 60, 0.14); font-weight: 600;"
            if s.startswith("▼"):
                return "color: #38BDF8; background-color: rgba(56, 189, 248, 0.14); font-weight: 600;"
            return "color: #94A3B8;"

        styled_df = df_det.style.format(format_dict)
        if diff_columns:
            styled_df = styled_df.map(style_diff_cell, subset=diff_columns)
        if evol_columns:
            styled_df = styled_df.map(style_evol_cell, subset=evol_columns)

        st.dataframe(styled_df, use_container_width=True, hide_index=True)


# =========================================================================
# STRUTTURA A 3 TAB
# =========================================================================
tab_report, tab_prodotti, tab_competitor = st.tabs(
    [
        ":material/dashboard: Report & Posizionamento",
        ":material/inventory_2: Gestione Prodotti",
        ":material/storefront: Gestione Competitor & Link",
    ]
)

# -------------------------------------------------------------------------
# TAB 1: REPORT & POSIZIONAMENTO
# -------------------------------------------------------------------------
with tab_report:
    if not prodotti:
        st.info(
            "Non ci sono ancora prodotti a catalogo. Vai nel tab **Gestione Prodotti** "
            "per aggiungere i tuoi prodotti manualmente o tramite CSV.",
            icon=":material/info:",
        )
    else:
        dettaglio_tipo = st.session_state.get("prezzi_dettaglio_tipo")
        dettaglio_valore = st.session_state.get("prezzi_dettaglio_valore")

        if dettaglio_tipo and dettaglio_valore:
            render_detail_table(dettaglio_tipo, dettaglio_valore, prodotti, competitors)
            st.divider()

        st.subheader(":material/category: Sintesi per Categoria")
        st.caption(
            "Differenza media % dei competitor rispetto al nostro prezzo "
            "(Verde = competitor uguale o più caro, Rosso/Arancio = competitor più economico)."
        )
        render_summary_grid("categoria", "Categoria", prodotti, competitors)

        st.divider()

        st.subheader(":material/branding_watermark: Sintesi per Marchio")
        st.caption(
            "Confronto medio % di posizionamento prezzi aggregato per ciascun marchio a catalogo."
        )
        render_summary_grid("marchio", "Marchio", prodotti, competitors)


# -------------------------------------------------------------------------
# TAB 2: GESTIONE PRODOTTI
# -------------------------------------------------------------------------
with tab_prodotti:
    col_add, col_csv = st.columns(2)

    # 1. Aggiungi Prodotto Manuale
    with col_add:
        with st.container(border=True):
            st.subheader(":material/add_box: Aggiungi Prodotto Manuale")
            with st.form("form_aggiungi_prodotto", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    in_sku = st.text_input(
                        "Codice / SKU (Opzionale)", placeholder="Es. IRR-001 (opzionale)"
                    )
                    in_categoria = st.text_input("Categoria", placeholder="Es. Centraline")
                with c2:
                    in_mio_prezzo = st.number_input(
                        "Mio Prezzo (€) *", min_value=0.0, step=0.5, format="%.2f"
                    )
                    in_marchio = st.text_input("Marchio", placeholder="Es. Hunter")

                in_descrizione = st.text_input(
                    "Descrizione *",
                    placeholder="Es. Centralina di irrigazione 6 stazioni indoor",
                )
                in_mio_url = st.text_input(
                    "URL Mio Prodotto (Opzionale - se vuoto lo cerca l'AI)",
                    placeholder="https://www.mondoirrigazione.it/...",
                )

                sub_prod = st.form_submit_button(
                    "Salva Prodotto",
                    icon=":material/save:",
                    type="primary",
                    use_container_width=True,
                )

                if sub_prod:
                    if not in_descrizione.strip():
                        st.warning(
                            "Il campo Descrizione è obbligatorio.",
                            icon=":material/warning:",
                        )
                    else:
                        try:
                            upsert_prodotto(
                                sku=in_sku,
                                descrizione=in_descrizione,
                                categoria=in_categoria,
                                marchio=in_marchio,
                                mio_prezzo=in_mio_prezzo,
                                mio_url=in_mio_url,
                            )
                            st.toast(
                                f"Prodotto '{in_descrizione.strip()}' salvato correttamente!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Errore nel salvataggio del prodotto: {exc}",
                                icon=":material/error:",
                            )

    # 2. Carica Prodotti da CSV
    with col_csv:
        with st.container(border=True):
            st.subheader(":material/upload_file: Carica / Esporta Prodotti CSV")
            st.caption(
                "Carica un file CSV con le colonne: `sku, descrizione, categoria, marchio, mio_prezzo, mio_url`. "
                "I campi `sku`, `mio_prezzo` (se vuoto diventa `0.0` e viene rilevato dallo scraper) e `mio_url` sono opzionali."
            )

            csv_template = (
                "sku,descrizione,categoria,marchio,mio_prezzo,mio_url\n"
                "IRR-101,Centralina X-Core 4 Stazioni,Centraline,Hunter,64.90,https://www.mondoirrigazione.it/centralina-x-core-4\n"
                ",Elettrovalvola PGV 1 pollice,Elettrovalvole,Hunter,,\n"
                "IRR-201,Programmatore ESP-RZXe 6 zone WiFi,Centraline,Rain Bird,0,\n"
            )

            export_rows = [
                {
                    "sku": p.get("sku") or "",
                    "descrizione": p.get("descrizione") or "",
                    "categoria": p.get("categoria") or "",
                    "marchio": p.get("marchio") or "",
                    "mio_prezzo": (
                        f"{float(p.get('mio_prezzo') or 0.0):.2f}"
                        if p.get("mio_prezzo") is not None
                        else "0.00"
                    ),
                    "mio_url": p.get("mio_url") or "",
                }
                for p in prodotti
            ]
            df_export_catalog = pd.DataFrame(
                export_rows,
                columns=["sku", "descrizione", "categoria", "marchio", "mio_prezzo", "mio_url"],
            )
            csv_full_catalog = df_export_catalog.to_csv(index=False).encode("utf-8-sig")

            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                st.download_button(
                    label="Scarica template CSV di esempio",
                    data=csv_template.encode("utf-8"),
                    file_name="template_prodotti.csv",
                    mime="text/csv",
                    icon=":material/download:",
                    use_container_width=True,
                    key="btn_dl_template_prodotti",
                )
            with dl_col2:
                st.download_button(
                    label="Esporta Catalogo Completo CSV",
                    data=csv_full_catalog,
                    file_name="catalogo_prodotti_completo.csv",
                    mime="text/csv",
                    icon=":material/download:",
                    use_container_width=True,
                    key="btn_dl_catalogo_completo_csv",
                )

            uploaded_csv = st.file_uploader(
                "Seleziona file CSV",
                type=["csv"],
                key="csv_uploader_prodotti",
            )

            if uploaded_csv is not None:
                if st.button(
                    "Importa Prodotti dal CSV",
                    icon=":material/cloud_upload:",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        df_csv = read_csv_flexible(uploaded_csv)

                        required_cols = {"descrizione"}
                        missing_cols = required_cols - set(df_csv.columns)
                        if missing_cols:
                            st.error(
                                f"Colonne mancanti nel CSV: {', '.join(sorted(missing_cols))}",
                                icon=":material/error:",
                            )
                        else:
                            for opt_col in ("sku", "categoria", "marchio", "mio_prezzo", "mio_url"):
                                if opt_col not in df_csv.columns:
                                    df_csv[opt_col] = None

                            def _parse_csv_mio_prezzo(val: Any) -> float:
                                if val is None or pd.isna(val):
                                    return 0.0
                                s = (
                                    str(val)
                                    .replace("€", "")
                                    .replace("EUR", "")
                                    .replace("\xa0", " ")
                                    .strip()
                                )
                                if not s or s.lower() in ("nan", "none", "null", "—", "-"):
                                    return 0.0
                                if "," in s and "." in s:
                                    if s.rfind(",") > s.rfind("."):
                                        s = s.replace(".", "").replace(",", ".")
                                    else:
                                        s = s.replace(",", "")
                                elif "," in s:
                                    s = s.replace(",", ".")
                                try:
                                    num = float(s)
                                    return round(num, 2) if num >= 0 and not pd.isna(num) else 0.0
                                except (ValueError, TypeError):
                                    return 0.0

                            df_csv["mio_prezzo"] = df_csv["mio_prezzo"].apply(_parse_csv_mio_prezzo)
                            df_csv = df_csv.dropna(subset=["descrizione"])

                            records = []
                            for _, r in df_csv.iterrows():
                                desc_val = str(r.get("descrizione") or "").strip()
                                if not desc_val:
                                    continue
                                raw_sku = r.get("sku")
                                clean_sku = (
                                    None
                                    if raw_sku is None or pd.isna(raw_sku) or not str(raw_sku).strip()
                                    else str(raw_sku).strip()
                                )
                                raw_url = r.get("mio_url")
                                clean_url = (
                                    None
                                    if raw_url is None or pd.isna(raw_url) or not str(raw_url).strip()
                                    else str(raw_url).strip()
                                )
                                raw_cat = r.get("categoria")
                                raw_mar = r.get("marchio")
                                records.append(
                                    {
                                        "sku": clean_sku,
                                        "descrizione": desc_val,
                                        "categoria": (
                                            None
                                            if raw_cat is None or pd.isna(raw_cat) or not str(raw_cat).strip()
                                            else str(raw_cat).strip()
                                        ),
                                        "marchio": (
                                            None
                                            if raw_mar is None or pd.isna(raw_mar) or not str(raw_mar).strip()
                                            else str(raw_mar).strip()
                                        ),
                                        "mio_prezzo": _parse_csv_mio_prezzo(r.get("mio_prezzo")),
                                        "mio_url": clean_url,
                                    }
                                )

                            bulk_upsert_prodotti(records)
                            st.toast(
                                f"Importati/aggiornati {len(records)} prodotti!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                    except Exception as csv_exc:
                        st.error(
                            f"Errore durante l'importazione del CSV: {csv_exc}",
                            icon=":material/error:",
                        )

    st.divider()

    # 3. Catalogo Prodotti con filtri rapidi, ordinamento e pulsanti "Modifica" ed "Elimina"
    st.subheader(f":material/list_alt: Catalogo Prodotti ({len(prodotti)})")

    if not prodotti:
        st.info("Nessun prodotto presente a database.", icon=":material/info:")
    else:
        categorie_disponibili = ["Tutte"] + sorted(
            {
                (p.get("categoria") or "Generale").strip() or "Generale"
                for p in prodotti
            },
            key=lambda s: s.lower(),
        )
        marchi_disponibili = ["Tutti"] + sorted(
            {
                (p.get("marchio") or "Generico").strip() or "Generico"
                for p in prodotti
            },
            key=lambda s: s.lower(),
        )
        opzioni_ordinamento = [
            "Nome (Descrizione A-Z)",
            "Nome (Z-A)",
            "Categoria",
            "Marchio",
            "SKU",
            "Prezzo (Crescente)",
            "Prezzo (Decrescente)",
        ]

        with st.container(border=True):
            f_col1, f_col2, f_col3, f_col4 = st.columns([1.6, 1.2, 1.2, 1.5])
            with f_col1:
                search_tab2 = st.text_input(
                    "Cerca prodotto o SKU",
                    placeholder="Filtra per descrizione, codice, marchio...",
                    key="txt_search_prod_tab2",
                )
            with f_col2:
                cat_tab2 = st.selectbox(
                    "Filtro Categoria",
                    options=categorie_disponibili,
                    key="sel_cat_prod_tab2",
                )
            with f_col3:
                mar_tab2 = st.selectbox(
                    "Filtro Marchio",
                    options=marchi_disponibili,
                    key="sel_mar_prod_tab2",
                )
            with f_col4:
                sort_tab2 = st.selectbox(
                    "Ordina per:",
                    options=opzioni_ordinamento,
                    key="sel_sort_prod_tab2",
                )

        prodotti_tab2 = filtra_e_ordina_prodotti(
            prodotti,
            search_query=search_tab2,
            cat_filter=cat_tab2,
            mar_filter=mar_tab2,
            sort_by=sort_tab2,
        )

        st.caption(f"Visualizzati **{len(prodotti_tab2)}** di **{len(prodotti)}** prodotti")

        h_cols = st.columns([1.2, 3, 1.5, 1.5, 1.2, 1, 1], vertical_alignment="center")
        h_cols[0].caption("**SKU**")
        h_cols[1].caption("**Descrizione**")
        h_cols[2].caption("**Categoria**")
        h_cols[3].caption("**Marchio**")
        h_cols[4].caption("**Mio Prezzo (€)**")
        h_cols[5].caption("**Modifica**")
        h_cols[6].caption("**Elimina**")

        for p in prodotti_tab2:
            p_id = str(p["id"])
            sku_display = f"`{p['sku']}`" if p.get("sku") else "—"
            mio_url_val = (p.get("mio_url") or "").strip()
            desc_text = f"**{p.get('descrizione', '')}**"
            if mio_url_val:
                desc_text += f" [:material/open_in_new:]({mio_url_val})"

            with st.container(border=True):
                r_cols = st.columns([1.2, 3, 1.5, 1.5, 1.2, 1, 1], vertical_alignment="center")
                r_cols[0].markdown(sku_display)
                r_cols[1].markdown(desc_text)
                r_cols[2].write(p.get("categoria") or "—")
                r_cols[3].write(p.get("marchio") or "—")
                r_cols[4].markdown(f"**€ {float(p.get('mio_prezzo') or 0.0):.2f}**")

                with r_cols[5]:
                    if st.button(
                        "Modifica",
                        icon=":material/edit:",
                        key=f"btn_edit_prod_{p_id}",
                        use_container_width=True,
                    ):
                        dialog_modifica_prodotto(p)

                with r_cols[6]:
                    if st.button(
                        "Elimina",
                        icon=":material/delete:",
                        key=f"btn_del_prod_{p_id}",
                        use_container_width=True,
                    ):
                        dialog_elimina_prodotto(p)


# -------------------------------------------------------------------------
# TAB 3: GESTIONE COMPETITOR & LINK
# -------------------------------------------------------------------------
with tab_competitor:
    # Pulsante Principale Sicuro: Aggiorna Solo Prezzi (Dai Link Esistenti)
    with st.container(border=True):
        st.markdown("#### :material/sync: Aggiornamento Prezzi (Dai Link Esistenti)")
        st.caption(
            "Rilegge in parallelo i prezzi esclusivamente dagli URL già presenti a database (`mio_url` e `url_prodotto`), "
            "senza cercare link mancanti e senza modificare mai gli URL esistenti."
        )
        run_prices_only = st.button(
            "Aggiorna Solo Prezzi (Dai Link Esistenti)",
            icon=":material/sync:",
            type="primary",
            use_container_width=True,
            key="btn_tab3_prices_only",
        )

    # Strumenti Avanzati: Ricerca Automatica Link Mancanti isolata in expander chiuso con conferma obbligatoria
    with st.expander(
        ":material/warning: Strumenti Avanzati: Ricerca Automatica Link Mancanti (Usa con cautela)",
        expanded=False,
    ):
        st.caption(
            "Cerca automaticamente con Serper.dev / AI solo gli URL ancora lasciati completamente vuoti nel database. "
            "Gli URL già inseriti non vengono mai sovrascritti."
        )
        confirm_autodiscovery = st.checkbox(
            "Confermo di voler cercare automaticamente gli URL lasciati vuoti",
            value=False,
            key="chk_confirm_autodiscovery",
        )
        run_autodiscovery = st.button(
            "Cerca Link Mancanti + Aggiorna Prezzi",
            icon=":material/travel_explore:",
            type="secondary",
            disabled=not confirm_autodiscovery,
            use_container_width=True,
            key="btn_tab3_autodiscovery",
        )

    if (run_autodiscovery and confirm_autodiscovery) or run_prices_only:
        discover_mode = bool(run_autodiscovery and confirm_autodiscovery)
        prog_bar = st.progress(
            0.0,
            text=(
                "Avvio ricerca link mancanti e lettura prezzi..."
                if discover_mode
                else "Lettura prezzi dai link esistenti..."
            ),
        )

        def _update_prog(pct: float, msg: str) -> None:
            prog_bar.progress(pct, text=msg)

        try:
            stats = esegui_auto_discovery_e_scraping(
                progress_callback=_update_prog,
                cerca_link_mancanti=discover_mode,
                discover_missing_urls=discover_mode,
            )
            prog_bar.empty()
            st.session_state["last_scan_log"] = stats.get("logs", [])
            st.session_state["last_scan_summary"] = (
                f"Nuovi URL trovati: {stats['miei_url_trovati'] + stats['comp_url_trovati']} • "
                f"Prezzi rilevati: {stats['miei_prezzi_aggiornati'] + stats['comp_prezzi_aggiornati']}"
            )
            st.toast(
                f"Completato! {st.session_state['last_scan_summary']}",
                icon=":material/check_circle:",
            )
            st.rerun()
        except Exception as exc:
            prog_bar.empty()
            st.error(f"Errore durante l'aggiornamento: {exc}", icon=":material/error:")

    if st.session_state.get("last_scan_log"):
        with st.expander(":material/receipt_long: Risultati scansione", expanded=True):
            if st.session_state.get("last_scan_summary"):
                st.caption(st.session_state["last_scan_summary"])
            for log_line in st.session_state["last_scan_log"]:
                st.markdown(f"- {log_line}")
            if st.button(
                "Nascondi log scansione",
                icon=":material/close:",
                key="btn_clear_scan_log",
            ):
                st.session_state.pop("last_scan_log", None)
                st.session_state.pop("last_scan_summary", None)
                st.rerun()

    st.divider()

    col_comp_form, col_comp_list = st.columns([1, 1.5])

    # 1. Aggiungi / Modifica / Elimina Competitor
    with col_comp_form:
        with st.container(border=True):
            st.subheader(":material/domain_add: Aggiungi Competitor")
            with st.form("form_nuovo_competitor", clear_on_submit=True):
                comp_nome = st.text_input("Nome Competitor *", placeholder="Es. IrrigazioneShop")
                comp_dominio = st.text_input(
                    "Dominio / Sito Web *", placeholder="https://www.irrigazioneshop.it"
                )
                add_comp_btn = st.form_submit_button(
                    "Aggiungi Competitor",
                    icon=":material/add:",
                    type="primary",
                    use_container_width=True,
                )

                if add_comp_btn:
                    if not comp_nome.strip() or not comp_dominio.strip():
                        st.warning(
                            "Inserisci sia il nome che il dominio del competitor.",
                            icon=":material/warning:",
                        )
                    else:
                        try:
                            add_competitor_prezzi(comp_nome, comp_dominio)
                            st.toast(
                                f"Competitor '{comp_nome.strip()}' aggiunto!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Errore nell'inserimento del competitor: {exc}",
                                icon=":material/error:",
                            )

    with col_comp_list:
        with st.container(border=True):
            st.subheader(f":material/storefront: Competitor Attivi ({len(competitors)})")
            if not competitors:
                st.info("Nessun competitor configurato.", icon=":material/info:")
            else:
                for comp in competitors:
                    c_id = str(comp["id"])
                    c_nome = comp.get("nome", "")
                    c_dom = comp.get("dominio", "")
                    with st.container(border=True):
                        c_info, c_edit, c_del = st.columns(
                            [2.6, 1, 1], vertical_alignment="center"
                        )
                        with c_info:
                            st.markdown(f"**{c_nome}**")
                            st.caption(c_dom)
                        with c_edit:
                            if st.button(
                                "Modifica",
                                icon=":material/edit:",
                                key=f"edit_comp_{c_id}",
                                use_container_width=True,
                            ):
                                dialog_modifica_competitor(comp)
                        with c_del:
                            if st.button(
                                "Elimina",
                                icon=":material/delete:",
                                key=f"del_comp_{c_id}",
                                use_container_width=True,
                            ):
                                dialog_elimina_competitor(comp)

    st.divider()

    # 2. Associazione e Modifica Link / Prezzi per Prodotto (Righe + Popup Modifica)
    st.subheader(":material/link: Associazione Link e Prezzi per Prodotto")
    st.caption(
        "Seleziona un competitor e clicca su **Modifica** accanto a qualsiasi prodotto per "
        "incollare/correggere l'URL e rileggere subito il prezzo dal sito o inserirlo manualmente."
    )

    if not competitors:
        st.info(
            "Aggiungi almeno un competitor sopra per associare link e prezzi.",
            icon=":material/info:",
        )
    elif not prodotti:
        st.info(
            "Aggiungi almeno un prodotto nel tab **Gestione Prodotti**.",
            icon=":material/info:",
        )
    else:
        comp_by_id = {str(c["id"]): c for c in competitors}
        comp_options = {str(c["id"]): f"{c['nome']} ({c.get('dominio', '')})" for c in competitors}

        cat_opts_tab3 = ["Tutte"] + sorted(
            {
                (p.get("categoria") or "Generale").strip() or "Generale"
                for p in prodotti
            },
            key=lambda s: s.lower(),
        )
        mar_opts_tab3 = ["Tutti"] + sorted(
            {
                (p.get("marchio") or "Generico").strip() or "Generico"
                for p in prodotti
            },
            key=lambda s: s.lower(),
        )
        sort_opts_tab3 = [
            "Nome (Descrizione A-Z)",
            "Nome (Z-A)",
            "Categoria",
            "Marchio",
            "SKU",
            "Prezzo (Crescente)",
            "Prezzo (Decrescente)",
        ]

        with st.container(border=True):
            ctrl_c1, ctrl_c2, ctrl_c3, ctrl_c4, ctrl_c5 = st.columns([1.5, 1.3, 1.0, 1.0, 1.4])
            with ctrl_c1:
                selected_comp_id = str(
                    st.selectbox(
                        "Seleziona Competitor",
                        options=list(comp_options.keys()),
                        format_func=lambda cid: comp_options[str(cid)],
                        key="sel_competitor_matrix",
                    )
                )
            with ctrl_c2:
                search_tab3 = st.text_input(
                    "Cerca prodotto o SKU",
                    placeholder="Filtra prodotti...",
                    key="txt_search_prod_tab3",
                )
            with ctrl_c3:
                cat_tab3 = st.selectbox(
                    "Categoria",
                    options=cat_opts_tab3,
                    key="sel_cat_prod_tab3",
                )
            with ctrl_c4:
                mar_tab3 = st.selectbox(
                    "Marchio",
                    options=mar_opts_tab3,
                    key="sel_mar_prod_tab3",
                )
            with ctrl_c5:
                sort_tab3 = st.selectbox(
                    "Ordina per:",
                    options=sort_opts_tab3,
                    key="sel_sort_prod_tab3",
                )

        selected_comp_obj = comp_by_id[selected_comp_id]
        prodotti_tab3 = filtra_e_ordina_prodotti(
            prodotti,
            search_query=search_tab3,
            cat_filter=cat_tab3,
            mar_filter=mar_tab3,
            sort_by=sort_tab3,
        )

        st.caption(f"Visualizzati **{len(prodotti_tab3)}** di **{len(prodotti)}** prodotti")

        # Intestazione colonne
        lh_cols = st.columns([0.9, 2.3, 1.0, 1.3, 1.1, 1.1, 1.3, 1.0], vertical_alignment="center")
        lh_cols[0].caption("**SKU**")
        lh_cols[1].caption("**Descrizione**")
        lh_cols[2].caption("**Mio Prezzo (€)**")
        lh_cols[3].caption("**URL Competitor**")
        lh_cols[4].caption("**Prezzo Comp. (€)**")
        lh_cols[5].caption("**Diff. %**")
        lh_cols[6].caption("**Evoluzione (vs prec.)**")
        lh_cols[7].caption("**Azione**")

        for p in prodotti_tab3:
            p_id = str(p["id"])
            sku_disp = f"`{p['sku']}`" if p.get("sku") else "—"
            desc_disp = p.get("descrizione", "")
            cat_disp = p.get("categoria") or "Generale"
            mar_disp = p.get("marchio") or "Generico"
            mio_p = float(p.get("mio_prezzo") or 0.0)

            rec = assoc_map.get((p_id, selected_comp_id), {})
            comp_url = (rec.get("url_prodotto") or "").strip()
            comp_p = (
                float(rec["ultimo_prezzo"])
                if rec.get("ultimo_prezzo") is not None
                else None
            )
            comp_prev_p = (
                float(rec["prezzo_precedente"])
                if rec.get("prezzo_precedente") is not None
                else None
            )
            diff_pct = calcola_diff_pct(mio_p, comp_p)
            evol_txt_row = format_evoluzione_text(comp_p, comp_prev_p)
            if evol_txt_row.startswith("▲"):
                sym_inline = '<span style="color:#FB923C; font-weight:700; margin-left:4px;">▲</span>'
            elif evol_txt_row.startswith("▼"):
                sym_inline = '<span style="color:#38BDF8; font-weight:700; margin-left:4px;">▼</span>'
            elif diff_pct is not None:
                sym_inline = '<span style="color:#94A3B8; font-weight:600; margin-left:4px;">=</span>'
            else:
                sym_inline = ""

            with st.container(border=True):
                lr_cols = st.columns(
                    [0.9, 2.3, 1.0, 1.3, 1.1, 1.1, 1.3, 1.0], vertical_alignment="center"
                )
                lr_cols[0].markdown(sku_disp)
                lr_cols[1].markdown(
                    f"**{desc_disp}**<br><span style='color:#94A3B8;font-size:0.78rem;'>{cat_disp} • {mar_disp}</span>",
                    unsafe_allow_html=True,
                )
                lr_cols[2].markdown(f"€ {mio_p:.2f}")

                if comp_url:
                    lr_cols[3].markdown(f"[:material/open_in_new: Apri scheda]({comp_url})")
                else:
                    lr_cols[3].caption("Non associato")

                if comp_p is not None and comp_p > 0:
                    lr_cols[4].markdown(f"**€ {comp_p:.2f}**")
                else:
                    lr_cols[4].caption("N/D")

                lr_cols[5].markdown(
                    f"{badge_diff_html(diff_pct)}{sym_inline}",
                    unsafe_allow_html=True,
                )
                lr_cols[6].markdown(
                    badge_evoluzione_html(comp_p, comp_prev_p), unsafe_allow_html=True
                )

                with lr_cols[7]:
                    if st.button(
                        "Modifica",
                        icon=":material/edit:",
                        key=f"btn_edit_link_{p_id}_{selected_comp_id}",
                        use_container_width=True,
                    ):
                        dialog_modifica_link_competitor(p, selected_comp_obj, rec)
