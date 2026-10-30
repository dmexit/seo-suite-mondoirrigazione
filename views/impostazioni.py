from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

from db import get_impostazioni, get_supabase_admin_client, update_impostazioni
from mailer import (
    esegui_aggiornamento_globale_veloce,
    genera_html_report,
    invia_email_report,
)

st.header(":material/settings: Impostazioni")
st.caption(
    "Configura il profilo aziendale, il server SMTP, l'invio istantaneo o programmato dei report email e gli accessi utente."
)

impostazioni = get_impostazioni()

tab_generali, tab_report = st.tabs(
    [
        ":material/tune: Generali & Brand",
        ":material/mail: Report Email & Automazione",
    ]
)

# =========================================================================
# TAB 1: GENERALI & BRAND (+ GESTIONE UTENTI)
# =========================================================================
with tab_generali:
    with st.container(border=True):
        st.subheader(":material/apartment: Informazioni Brand & Dominio")

        with st.form("form_profilo_azienda"):
            c_b1, c_b2 = st.columns(2)
            with c_b1:
                nome_azienda = st.text_input(
                    "Nome Azienda / Brand",
                    value=impostazioni.get("nome_azienda") or "",
                    placeholder="Es. Mondoirrigazione",
                )
            with c_b2:
                sito_web = st.text_input(
                    "Sito Web Ufficiale",
                    value=impostazioni.get("sito_web") or "https://www.mondoirrigazione.it",
                    placeholder="https://www.mondoirrigazione.it",
                )

            logo_url = st.text_input(
                "URL Logo Aziendale",
                value=impostazioni.get("logo_url") or "",
                placeholder="https://www.mondoirrigazione.it/logo.png",
            )

            if logo_url.strip():
                st.caption("Anteprima Logo:")
                try:
                    st.image(logo_url.strip(), width=160)
                except Exception:
                    st.warning(
                        "Impossibile caricare l'anteprima dal link fornito.",
                        icon=":material/warning:",
                    )

            salva_profilo = st.form_submit_button(
                "Salva Profilo Azienda",
                icon=":material/save:",
                type="primary",
                use_container_width=True,
            )

            if salva_profilo:
                try:
                    update_impostazioni(
                        {
                            "nome_azienda": nome_azienda.strip(),
                            "sito_web": sito_web.strip(),
                            "logo_url": logo_url.strip(),
                        }
                    )
                    st.toast(
                        "Profilo azienda aggiornato con successo!",
                        icon=":material/check_circle:",
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(
                        f"Errore durante il salvataggio del profilo: {exc}",
                        icon=":material/error:",
                    )

    st.divider()

    with st.container(border=True):
        st.subheader(":material/group: Gestione Utenti Abilitati")

        admin_client = get_supabase_admin_client()

        if admin_client is None:
            st.warning(
                "Per gestire gli utenti è necessario configurare `SUPABASE_SERVICE_ROLE_KEY` "
                "nel file `.env`.",
                icon=":material/key:",
            )
        else:
            try:
                users_response = admin_client.auth.admin.list_users()
                users_list = (
                    users_response
                    if isinstance(users_response, list)
                    else getattr(users_response, "users", [])
                )

                current_user = st.session_state.get("user")
                current_user_id = getattr(current_user, "id", None)

                if not users_list:
                    st.info("Nessun utente trovato.", icon=":material/info:")
                else:
                    for u in users_list:
                        u_id = getattr(u, "id", "")
                        u_email = getattr(u, "email", "Senza email")
                        u_created = getattr(u, "created_at", None)
                        created_str = ""
                        if u_created:
                            try:
                                created_str = u_created.strftime("%d/%m/%Y %H:%M")
                            except Exception:
                                created_str = str(u_created)[:16]

                        with st.container(border=True):
                            col_info, col_action = st.columns(
                                [4, 1], vertical_alignment="center"
                            )
                            with col_info:
                                badge = " *(Tu)*" if u_id == current_user_id else ""
                                st.markdown(f"**{u_email}**{badge}")
                                if created_str:
                                    st.caption(f"Creato il: {created_str} • ID: `{u_id}`")
                            with col_action:
                                disable_delete = u_id == current_user_id
                                if st.button(
                                    "Elimina",
                                    icon=":material/delete:",
                                    key=f"del_user_{u_id}",
                                    use_container_width=True,
                                    disabled=disable_delete,
                                    help=(
                                        "Non puoi eliminare l'utente con cui sei attualmente loggato."
                                        if disable_delete
                                        else "Elimina questo utente"
                                    ),
                                ):
                                    try:
                                        admin_client.auth.admin.delete_user(u_id)
                                        st.toast(
                                            f"Utente {u_email} eliminato.",
                                            icon=":material/delete:",
                                        )
                                        st.rerun()
                                    except Exception as del_exc:
                                        st.error(
                                            f"Errore durante l'eliminazione: {del_exc}",
                                            icon=":material/error:",
                                        )
            except Exception as list_exc:
                st.error(
                    f"Impossibile recuperare la lista utenti: {list_exc}",
                    icon=":material/error:",
                )

            st.markdown("##### :material/person_add: Aggiungi Nuovo Utente")
            with st.form("form_nuovo_utente", clear_on_submit=True):
                u_c1, u_c2 = st.columns(2)
                with u_c1:
                    nuova_email = st.text_input(
                        "Email nuovo utente", placeholder="utente@azienda.it"
                    )
                with u_c2:
                    nuova_password = st.text_input(
                        "Password temporanea",
                        type="password",
                        placeholder="Minimo 6 caratteri",
                    )
                crea_btn = st.form_submit_button(
                    "Crea Utente",
                    icon=":material/person_add:",
                    type="primary",
                    use_container_width=True,
                )

                if crea_btn:
                    if not nuova_email.strip() or not nuova_password:
                        st.warning(
                            "Compila sia l'email che la password.",
                            icon=":material/warning:",
                        )
                    else:
                        try:
                            admin_client.auth.admin.create_user(
                                {
                                    "email": nuova_email.strip(),
                                    "password": nuova_password,
                                    "email_confirm": True,
                                }
                            )
                            st.toast(
                                f"Utente {nuova_email.strip()} creato e confermato!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as create_exc:
                            st.error(
                                f"Errore nella creazione dell'utente: {create_exc}",
                                icon=":material/error:",
                            )


# =========================================================================
# TAB 2: REPORT EMAIL & AUTOMAZIONE
# =========================================================================
with tab_report:
    # ---------------------------------------------------------------------
    # 1. FORM CONFIGURAZIONE SMTP & DESTINATARI
    # ---------------------------------------------------------------------
    with st.container(border=True):
        st.subheader(":material/settings_ethernet: Configurazione SMTP & Destinatari")
        st.caption(
            "Configura i parametri del server di posta in uscita (es. Gmail con *Password per le App* a 16 caratteri) "
            "e gli indirizzi email che riceveranno il report riepilogativo."
        )

        opzioni_freq_report = ["Settimanale", "Giornaliero", "Disattivato"]
        raw_freq = str(
            impostazioni.get("report_frequenza")
            or impostazioni.get("frequenza_aggiornamento")
            or "Settimanale"
        ).strip()
        freq_map_norm = {
            "settimanale": "Settimanale",
            "giornaliero": "Giornaliero",
            "giornaliera": "Giornaliero",
            "disattivato": "Disattivato",
        }
        freq_selezionata = freq_map_norm.get(raw_freq.lower(), "Settimanale")
        idx_freq_rep = (
            opzioni_freq_report.index(freq_selezionata)
            if freq_selezionata in opzioni_freq_report
            else 0
        )

        try:
            default_port = int(impostazioni.get("smtp_port") or 587)
        except (ValueError, TypeError):
            default_port = 587

        with st.form("form_smtp_report_config"):
            col_s1, col_s2 = st.columns([2.2, 1])
            with col_s1:
                in_smtp_host = st.text_input(
                    "Server SMTP",
                    value=impostazioni.get("smtp_host") or "smtp.gmail.com",
                    placeholder="smtp.gmail.com",
                )
            with col_s2:
                in_smtp_port = st.number_input(
                    "Porta SMTP",
                    min_value=1,
                    max_value=65535,
                    value=default_port,
                    step=1,
                )

            col_u1, col_u2 = st.columns(2)
            with col_u1:
                in_smtp_user = st.text_input(
                    "Utente / Email SMTP",
                    value=impostazioni.get("smtp_user") or "",
                    placeholder="tuoindirizzo@gmail.com",
                )
            with col_u2:
                in_smtp_password = st.text_input(
                    "Password SMTP / Password per le App",
                    value=impostazioni.get("smtp_password") or "",
                    type="password",
                    placeholder="••••••••••••••••",
                )

            col_d1, col_d2 = st.columns([2.2, 1])
            with col_d1:
                in_email_dest = st.text_input(
                    "Email Destinatari Report (separate da virgola)",
                    value=impostazioni.get("email_destinatari") or "",
                    placeholder="direzione@mondoirrigazione.it, seo@mondoirrigazione.it",
                )
            with col_d2:
                in_report_freq = st.selectbox(
                    "Frequenza Report Automatico",
                    options=opzioni_freq_report,
                    index=idx_freq_rep,
                )

            salva_smtp = st.form_submit_button(
                "Salva Configurazione Email",
                icon=":material/save:",
                type="primary",
                use_container_width=True,
            )

            if salva_smtp:
                try:
                    update_impostazioni(
                        {
                            "smtp_host": in_smtp_host.strip() or "smtp.gmail.com",
                            "smtp_port": int(in_smtp_port),
                            "smtp_user": in_smtp_user.strip(),
                            "smtp_password": in_smtp_password.strip(),
                            "email_destinatari": in_email_dest.strip(),
                            "report_frequenza": in_report_freq,
                            "invia_report_email": in_report_freq != "Disattivato",
                        }
                    )
                    st.toast(
                        "Configurazione SMTP e destinatari salvata su Supabase!",
                        icon=":material/check_circle:",
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(
                        f"Errore durante il salvataggio della configurazione SMTP: {exc}",
                        icon=":material/error:",
                    )

    st.divider()

    # ---------------------------------------------------------------------
    # 2. AZIONI IMMEDIATE (INVIO ISTANTANEO + AGGIORNA TUTTO VELOCE & INVIA)
    # ---------------------------------------------------------------------
    with st.container(border=True):
        st.subheader(":material/bolt: Azioni Immediate & Invio Report")
        st.caption(
            "Invia subito il report HTML ai destinatari usando i dati già presenti a database, oppure esegui prima "
            "l'aggiornamento veloce completo (**Solo Prezzi salvati + Posizionamenti SEO + Verifica Backlink**)."
        )

        act_col1, act_col2 = st.columns(2)
        with act_col1:
            btn_send_now = st.button(
                "Invia Report Istantaneo (Dati Attuali)",
                icon=":material/send:",
                type="primary",
                use_container_width=True,
                key="btn_send_instant_report",
            )
        with act_col2:
            btn_update_and_send = st.button(
                "Aggiorna Tutto (Solo Prezzi + SEO + Backlink) e Invia Report",
                icon=":material/sync:",
                type="secondary",
                use_container_width=True,
                key="btn_update_all_and_send_report",
            )

        if btn_send_now:
            with st.spinner("Generazione report HTML e invio email in corso..."):
                html_rep = genera_html_report()
                st.session_state["cached_html_report_preview"] = html_rep
                ok, msg = invia_email_report(html_content=html_rep)
                if ok:
                    st.success(msg, icon=":material/check_circle:")
                else:
                    st.error(msg, icon=":material/error:")

        if btn_update_and_send:
            prog_bar = st.progress(
                0.0,
                text="Avvio aggiornamento globale veloce (Prezzi + SEO + Backlink)...",
            )

            def _upd_global_ui(pct: float, status_text: str) -> None:
                prog_bar.progress(min(max(pct, 0.0), 1.0), text=status_text)

            try:
                summary_stats = esegui_aggiornamento_globale_veloce(
                    progress_callback=_upd_global_ui
                )
                prog_bar.progress(1.0, text="Generazione report HTML e invio email...")
                html_rep = genera_html_report()
                st.session_state["cached_html_report_preview"] = html_rep
                ok, msg = invia_email_report(html_content=html_rep)
                prog_bar.empty()

                p_stat = summary_stats.get("prezzi", {})
                s_stat = summary_stats.get("seo", {})
                b_stat = summary_stats.get("backlinks", {})
                riepilogo_scan = (
                    f"Prezzi aggiornati: {p_stat.get('miei_prezzi_aggiornati', 0) + p_stat.get('comp_prezzi_aggiornati', 0)} • "
                    f"Keyword SEO analizzate: {s_stat.get('keywords_analizzate', 0)} • "
                    f"Backlink verificati: {b_stat.get('totale_verificati', 0)}"
                )

                if ok:
                    st.success(
                        f"Aggiornamento completato ({riepilogo_scan}). {msg}",
                        icon=":material/check_circle:",
                    )
                else:
                    st.warning(
                        f"Aggiornamento dati completato ({riepilogo_scan}), ma l'invio email non è riuscito: {msg}",
                        icon=":material/warning:",
                    )
            except Exception as exc:
                prog_bar.empty()
                st.error(
                    f"Errore durante l'aggiornamento globale: {exc}",
                    icon=":material/error:",
                )

        # Anteprima Report HTML espandibile direttamente in pagina
        with st.expander(":material/visibility: Visualizza Anteprima Report HTML", expanded=False):
            if st.button(
                "Rigenera Anteprima con i Dati Attuali",
                icon=":material/refresh:",
                key="btn_refresh_html_preview",
            ):
                st.session_state["cached_html_report_preview"] = genera_html_report()

            preview_html = st.session_state.get("cached_html_report_preview")
            if not preview_html:
                try:
                    preview_html = genera_html_report()
                    st.session_state["cached_html_report_preview"] = preview_html
                except Exception as prev_exc:
                    st.error(
                        f"Impossibile generare l'anteprima del report: {prev_exc}",
                        icon=":material/error:",
                    )
                    preview_html = None

            if preview_html:
                components.html(preview_html, height=780, scrolling=True)

    st.divider()

    # ---------------------------------------------------------------------
    # 3. BOX INFORMATIVO SCHEDULAZIONE AUTOMATICA (CRONTAB MAC / LINUX)
    # ---------------------------------------------------------------------
    with st.container(border=True):
        st.subheader(":material/schedule: Automazione tramite Crontab (Mac / Linux)")
        st.caption(
            "Puoi schedulare l'esecuzione automatica in background dello script `cron_report.py` "
            "(che aggiorna Prezzi salvati, SEO e Backlink e invia il report HTML via email) "
            "aggiungendo una regola al `crontab` di sistema (`crontab -e`)."
        )

        project_dir = str(Path(__file__).resolve().parent.parent)
        python_bin = f"{project_dir}/.venv/bin/python"
        script_path = f"{project_dir}/cron_report.py"
        log_path = f"{project_dir}/cron_report.log"

        cron_weekly = f'0 8 * * 1 "{python_bin}" "{script_path}" >> "{log_path}" 2>&1'
        cron_daily = f'0 8 * * * "{python_bin}" "{script_path}" >> "{log_path}" 2>&1'

        st.markdown("**Esempio 1 — Esecuzione Settimanale (ogni Lunedì alle 08:00):**")
        st.code(cron_weekly, language="bash")

        st.markdown("**Esempio 2 — Esecuzione Giornaliera (ogni giorno alle 08:00):**")
        st.code(cron_daily, language="bash")

        st.markdown("**Esecuzione manuale di test da terminale:**")
        st.code(f'"{python_bin}" "{script_path}"', language="bash")
