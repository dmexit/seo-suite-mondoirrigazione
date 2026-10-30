import streamlit as st
from db import get_impostazioni, sign_in, sign_out

st.set_page_config(
    page_title="SEO Suite Dashboard",
    page_icon=":material/analytics:",
    layout="wide",
    initial_sidebar_state="expanded",
)


def render_login() -> None:
    """Mostra il form di Login centrato quando l'utente non è autenticato."""
    st.markdown("<br><br>", unsafe_allow_html=True)
    _, col_center, _ = st.columns([1, 1.2, 1])

    with col_center:
        with st.container(border=True):
            st.markdown("### :material/lock: Accesso Dashboard")
            st.caption("Inserisci le tue credenziali per accedere alla SEO Suite.")

            with st.form("login_form"):
                email = st.text_input("Email", placeholder="nome@azienda.it")
                password = st.text_input("Password", type="password", placeholder="••••••••")
                submit = st.form_submit_button(
                    "Accedi",
                    type="primary",
                    icon=":material/login:",
                    use_container_width=True,
                )

                if submit:
                    if not email.strip() or not password:
                        st.warning(
                            "Inserisci sia l'email che la password.",
                            icon=":material/warning:",
                        )
                    else:
                        try:
                            sign_in(email.strip(), password)
                            get_impostazioni(force_refresh=True)
                            st.toast("Accesso effettuato!", icon=":material/check_circle:")
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Credenziali non valide o errore di connessione: {exc}",
                                icon=":material/error:",
                            )


def main() -> None:
    # 1. Controllo autenticazione
    if "user" not in st.session_state or st.session_state["user"] is None:
        render_login()
        return

    # 2. Caricamento dati di brand dalla tabella 'impostazioni' (id = 1)
    impostazioni = get_impostazioni()
    nome_azienda = impostazioni.get("nome_azienda") or "SEO Suite"
    logo_url = (impostazioni.get("logo_url") or "").strip()

    # 3. Definizione pagine di navigazione
    pages = [
        st.Page(
            "views/prezzi.py",
            title="Prezzi & Competitor",
            icon=":material/sell:",
            default=True,
        ),
        st.Page("views/seo.py", title="SEO", icon=":material/monitoring:"),
        st.Page("views/backlink.py", title="Backlink", icon=":material/link:"),
        st.Page("views/impostazioni.py", title="Impostazioni", icon=":material/settings:"),
    ]
    pg = st.navigation(pages)

    # 4. Sidebar personalizzata (solo brand, navigazione e pulsante Esci)
    with st.sidebar:
        if logo_url:
            try:
                st.image(logo_url, use_container_width=True)
            except Exception:
                pass
        st.markdown(f"### {nome_azienda}")
        st.divider()

        if st.button("Esci", icon=":material/logout:", use_container_width=True):
            sign_out()
            st.rerun()

    # 5. Esecuzione pagina selezionata
    pg.run()


if __name__ == "__main__":
    main()
