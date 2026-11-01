import streamlit as st
from db import get_impostazioni, get_supabase_client, sign_in, sign_out

st.set_page_config(
    page_title="SEO Suite Dashboard",
    page_icon=":material/analytics:",
    layout="wide",
    initial_sidebar_state="expanded",
)


LIGHT_VARS = """
    --bg-main: #f8fafc;
    --bg-sidebar: #ffffff;
    --bg-card: #ffffff;
    --text-main: #0f172a;
    --text-muted: #64748b;
    --border-color: #e2e8f0;
    --card-shadow: 0 1px 3px rgba(0,0,0,0.04);
    --logo-bg: #0f172a;
    --logo-pad: 10px 12px;
"""

DARK_VARS = """
    --bg-main: #0b0f19;
    --bg-sidebar: #0f172a;
    --bg-card: #111827;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --border-color: #1e293b;
    --card-shadow: none;
    --logo-bg: transparent;
    --logo-pad: 0;
"""


def inject_theme_css() -> None:
    """Inietta le variabili CSS del tema (Chiaro/Scuro) e gli override globali."""
    dark = st.session_state.get("dark_mode", False)
    variables = DARK_VARS if dark else LIGHT_VARS
    st.markdown(
        f"""
<style>
:root {{{variables}}}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
    background-color: var(--bg-main) !important;
    color: var(--text-main) !important;
}}
[data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child {{
    background-color: var(--bg-sidebar) !important;
    border-right: 1px solid var(--border-color);
}}
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp p, .stApp label, .stApp li,
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"] {{
    color: var(--text-main);
}}
[data-testid="stCaptionContainer"], .stApp small {{
    color: var(--text-muted) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"] {{
    background-color: var(--bg-card);
    border-color: var(--border-color) !important;
    box-shadow: var(--card-shadow);
}}
[data-testid="stMetric"] {{
    background-color: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 10px 14px;
    box-shadow: var(--card-shadow);
}}
[data-testid="stDataFrame"], [data-testid="stTable"] {{
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background-color: var(--bg-card);
    box-shadow: var(--card-shadow);
}}
[data-testid="stSidebar"] [data-testid="stImage"] img {{
    background: var(--logo-bg);
    padding: var(--logo-pad);
    border-radius: 10px;
    box-sizing: border-box;
}}
hr {{ border-color: var(--border-color) !important; }}
</style>
""",
        unsafe_allow_html=True,
    )


def render_login() -> None:
    """Mostra il form di Login centrato quando l'utente non è autenticato."""
    inject_theme_css()
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
                            res = sign_in(email.strip(), password)
                            if res and getattr(res, "session", None):
                                st.session_state["access_token"] = res.session.access_token
                                st.session_state["refresh_token"] = res.session.refresh_token
                            get_impostazioni(force_refresh=True)
                            st.toast("Accesso effettuato!", icon=":material/check_circle:")
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Credenziali non valide o errore di connessione: {exc}",
                                icon=":material/error:",
                            )


def main() -> None:
    if "dark_mode" not in st.session_state:
        st.session_state.dark_mode = False

    # 1. Controllo autenticazione e rinnovo automatico eventuale JWT scaduto
    if "user" not in st.session_state or st.session_state["user"] is None:
        render_login()
        return

    # Verifica/rinnova preventivamente il client Supabase in caso di JWT prossimo a scadenza
    get_supabase_client()

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

        st.toggle(
            "Tema Scuro",
            value=st.session_state.dark_mode,
            key="toggle_dark_mode",
        )
        st.session_state.dark_mode = bool(st.session_state.get("toggle_dark_mode", False))

        if st.button("Esci", icon=":material/logout:", use_container_width=True):
            sign_out()
            st.rerun()

    # 5. Applica tema (dopo il toggle, così il cambio è immediato)
    inject_theme_css()

    # 6. Esecuzione pagina selezionata
    pg.run()


if __name__ == "__main__":
    main()
