import base64
import io
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from postgrest.exceptions import APIError
from supabase import Client, create_client

load_dotenv(override=True)

_HEADLESS_CLIENT: Optional[Client] = None


def _has_streamlit_context() -> bool:
    """Verifica se il codice sta girando dentro una sessione attiva di Streamlit."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def get_env_or_secret(key: str, default: str = "") -> str:
    """
    Legge una variabile di configurazione provando:
    1. `os.getenv(key)` (da `.env` locale o variabili GitHub Actions).
    2. `st.secrets[key]` (su Streamlit Community Cloud), sincronizzandola in `os.environ`
       così anche eventuali worker thread secondari la trovano subito in `os.getenv`.
    Non solleva mai eccezioni se `secrets.toml` non esiste.
    """
    val = os.getenv(key, "").strip()
    if val:
        return val
    if _has_streamlit_context():
        try:
            if key in st.secrets:
                s_val = str(st.secrets[key]).strip()
                if s_val:
                    os.environ[key] = s_val
                    return s_val
        except Exception:
            pass
    return default


def _is_jwt_expired_token(token: Optional[str], leeway_seconds: int = 90) -> bool:
    """
    Decodifica il payload del JWT (senza verifica crittografica) per controllare `exp`.
    Restituisce True se il token è già scaduto o scadrà entro `leeway_seconds`.
    """
    if not token or "." not in token:
        return False
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return False
        payload_b64 = parts[1]
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        data = json.loads(decoded.decode("utf-8", errors="ignore"))
        exp = data.get("exp")
        if exp is not None and float(exp) - time.time() < leeway_seconds:
            return True
    except Exception:
        return False
    return False


def _is_jwt_expired_error(exc: Exception) -> bool:
    """Rileva se un'eccezione proviene da un token JWT scaduto (PGRST303 / 401 / JWT expired)."""
    msg = str(exc).lower()
    if "pgrst303" in msg or "jwt expired" in msg or "jwt" in msg or "token is expired" in msg:
        return True
    if isinstance(exc, APIError):
        code = str(getattr(exc, "code", "") or "").upper()
        message = str(getattr(exc, "message", "") or "").lower()
        if code == "PGRST303" or "jwt" in message or "expired" in message:
            return True
    return False


def _create_clean_data_client() -> Client:
    """
    Crea un client Supabase pulito (usando SUPABASE_SERVICE_ROLE_KEY se disponibile,
    altrimenti SUPABASE_KEY) senza alcun header Authorization utente scaduto.
    """
    url = get_env_or_secret("SUPABASE_URL")
    key = get_env_or_secret("SUPABASE_KEY")
    service_key = get_env_or_secret("SUPABASE_SERVICE_ROLE_KEY")
    effective_key = (
        service_key
        if (service_key and "inserisci_qui" not in service_key)
        else key
    )
    return create_client(url, effective_key)


def refresh_or_reset_supabase_session(force_clean_fallback: bool = False) -> Client:
    """
    Rinnova la sessione Supabase quando il token JWT è scaduto (PGRST303):
    1. Se `refresh_token` è presente in `st.session_state`, chiama `client.auth.refresh_session(refresh_token)`
       e aggiorna `access_token` e `refresh_token` in `st.session_state`.
    2. Se il refresh fallisce o `force_clean_fallback=True`, ricrea un client Supabase pulito
       senza l'header Authorization utente scaduto e lo salva in `st.session_state["supabase_client"]`.
    """
    global _HEADLESS_CLIENT
    url = get_env_or_secret("SUPABASE_URL")
    key = get_env_or_secret("SUPABASE_KEY")

    if not _has_streamlit_context():
        _HEADLESS_CLIENT = _create_clean_data_client()
        return _HEADLESS_CLIENT

    if not force_clean_fallback:
        refresh_token = st.session_state.get("refresh_token")
        if refresh_token:
            try:
                fresh_auth_client = create_client(url, key)
                res = fresh_auth_client.auth.refresh_session(refresh_token)
                if res and getattr(res, "session", None):
                    new_access = res.session.access_token
                    new_refresh = res.session.refresh_token
                    st.session_state["access_token"] = new_access
                    st.session_state["refresh_token"] = new_refresh
                    if getattr(res, "user", None):
                        st.session_state["user"] = res.user
                    fresh_auth_client.postgrest.auth(new_access)
                    st.session_state["supabase_client"] = fresh_auth_client
                    return fresh_auth_client
            except Exception:
                pass

    # Fallback: ricrea un client Supabase pulito senza l'header JWT scaduto
    clean_client = _create_clean_data_client()
    st.session_state.pop("access_token", None)
    st.session_state["supabase_client"] = clean_client
    return clean_client


def get_supabase_client() -> Client:
    """
    Restituisce il client Supabase:
    - In sessione Streamlit attiva: usa `st.session_state` con i token RLS dell'utente loggato,
      rinnovando preventivamente il JWT se è prossimo alla scadenza o già scaduto.
    - Da terminale / CLI (`cron_report.py`) o senza sessione utente: usa `SUPABASE_SERVICE_ROLE_KEY`
      (se presente nel `.env` / Secrets) o `SUPABASE_KEY` senza dipendere da `st.session_state`.
    """
    global _HEADLESS_CLIENT
    load_dotenv(override=True)

    url = get_env_or_secret("SUPABASE_URL")
    key = get_env_or_secret("SUPABASE_KEY")
    # Pre-sincronizza anche SERPER_API_KEY e GEMINI_API_KEY da st.secrets (se presenti su Streamlit Cloud)
    get_env_or_secret("SERPER_API_KEY")
    get_env_or_secret("GEMINI_API_KEY")

    if not url or not key or "tuo-progetto" in url or "inserisci_qui" in key:
        if _has_streamlit_context():
            st.error(
                "Credenziali Supabase mancanti o non configurate nel file `.env` o in `st.secrets`. "
                "Inserisci `SUPABASE_URL` e `SUPABASE_KEY`.",
                icon=":material/warning:",
            )
            st.stop()
        raise RuntimeError(
            "Credenziali Supabase mancanti (SUPABASE_URL / SUPABASE_KEY)."
        )

    if not _has_streamlit_context():
        if _HEADLESS_CLIENT is None:
            _HEADLESS_CLIENT = _create_clean_data_client()
        return _HEADLESS_CLIENT

    if "supabase_client" not in st.session_state:
        st.session_state["supabase_client"] = create_client(url, key)

    client: Client = st.session_state["supabase_client"]

    # Controlla ed eventualmente rinnova il token JWT se è scaduto
    access_token = st.session_state.get("access_token")
    refresh_token = st.session_state.get("refresh_token")
    if access_token:
        if _is_jwt_expired_token(access_token):
            client = refresh_or_reset_supabase_session(force_clean_fallback=False)
        else:
            try:
                client.postgrest.auth(access_token)
            except Exception:
                pass
    elif refresh_token:
        client = refresh_or_reset_supabase_session(force_clean_fallback=False)

    return client


def execute_with_jwt_retry(operation: Callable[[Client], Any]) -> Any:
    """
    Esegue qualsiasi operazione Supabase intercettando `APIError` / `PGRST303` ("JWT expired"):
    1. Tenta con il client corrente.
    2. Se il JWT è scaduto, chiama `refresh_or_reset_supabase_session()` e ripete subito la query.
    3. Se fallisce ancora per JWT, usa un client pulito senza header utente scaduto e ripete la query.
    """
    client = get_supabase_client()
    try:
        return operation(client)
    except Exception as exc:
        if not _is_jwt_expired_error(exc):
            raise
        try:
            refreshed_client = refresh_or_reset_supabase_session(force_clean_fallback=False)
            return operation(refreshed_client)
        except Exception as retry_exc:
            if not _is_jwt_expired_error(retry_exc):
                raise
            clean_client = refresh_or_reset_supabase_session(force_clean_fallback=True)
            return operation(clean_client)


def get_supabase_admin_client() -> Optional[Client]:
    """Restituisce il client Supabase Admin inizializzato con SUPABASE_SERVICE_ROLE_KEY."""
    url = get_env_or_secret("SUPABASE_URL")
    service_key = get_env_or_secret("SUPABASE_SERVICE_ROLE_KEY")

    if (
        not url
        or not service_key
        or "tuo-progetto" in url
        or "inserisci_qui" in service_key
    ):
        return None

    return create_client(url, service_key)


def sign_in(email: str, password: str) -> Any:
    """Autentica l'utente tramite email e password su Supabase Auth."""
    client = get_supabase_client()
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    if response and response.session:
        st.session_state["user"] = response.user
        st.session_state["access_token"] = response.session.access_token
        st.session_state["refresh_token"] = response.session.refresh_token
        client.postgrest.auth(response.session.access_token)
    return response


def sign_out() -> None:
    """Effettua il logout dell'utente e pulisce la sessione."""
    try:
        client = get_supabase_client()
        client.auth.sign_out()
    except Exception:
        pass

    for key in ["user", "access_token", "refresh_token", "impostazioni", "supabase_client"]:
        st.session_state.pop(key, None)


# =========================================================================
# IMPOSTAZIONI
# =========================================================================


def get_impostazioni(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Legge la riga id = 1 dalla tabella 'impostazioni', inclusi i campi SMTP e Report:
    `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `email_destinatari`, `report_frequenza`.
    """
    in_st = _has_streamlit_context()
    if in_st and not force_refresh and "impostazioni" in st.session_state:
        return st.session_state["impostazioni"]

    default_settings: Dict[str, Any] = {
        "id": 1,
        "nome_azienda": "SEO Suite Dashboard",
        "sito_web": "https://www.mondoirrigazione.it",
        "logo_url": "",
        "frequenza_aggiornamento": "giornaliera",
        "invia_report_email": False,
        "email_destinatari": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "report_frequenza": "Settimanale",
        "ultimo_aggiornamento": None,
    }

    try:
        res = execute_with_jwt_retry(
            lambda client: client.table("impostazioni")
            .select("*")
            .eq("id", 1)
            .maybe_single()
            .execute()
        )
        if res and getattr(res, "data", None):
            row = dict(res.data)
            # Valori di default se nulli su DB
            if not row.get("smtp_host"):
                row["smtp_host"] = default_settings["smtp_host"]
            if row.get("smtp_port") is None:
                row["smtp_port"] = default_settings["smtp_port"]
            if not row.get("report_frequenza"):
                row["report_frequenza"] = default_settings["report_frequenza"]
            data = {**default_settings, **row}
            if in_st:
                st.session_state["impostazioni"] = data
            return data
    except Exception as exc:
        if in_st:
            st.warning(
                f"Impossibile leggere la tabella `impostazioni`: {exc}",
                icon=":material/warning:",
            )

    if in_st:
        st.session_state["impostazioni"] = default_settings
    return default_settings


def update_impostazioni(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggiorna (o crea se assente) la riga id = 1 nella tabella 'impostazioni'.
    Supporta sia le impostazioni di brand sia le configurazioni SMTP e Report
    (`smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `email_destinatari`, `report_frequenza`).
    """
    cleaned_payload = dict(payload)
    if "smtp_port" in cleaned_payload and cleaned_payload["smtp_port"] is not None:
        try:
            cleaned_payload["smtp_port"] = int(cleaned_payload["smtp_port"])
        except (ValueError, TypeError):
            cleaned_payload["smtp_port"] = 587

    data_to_save = {"id": 1, **cleaned_payload}

    execute_with_jwt_retry(
        lambda client: client.table("impostazioni")
        .upsert(data_to_save, on_conflict="id")
        .execute()
    )
    return get_impostazioni(force_refresh=True)


def trigger_global_refresh() -> Dict[str, Any]:
    """Aggiorna il campo 'ultimo_aggiornamento' con l'orario corrente (NOW())."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return update_impostazioni({"ultimo_aggiornamento": now_iso})


# =========================================================================
# HELPER COMUNI
# =========================================================================


def _clean_nullable_str(val: Any) -> Optional[str]:
    """Converte valori vuoti, NaN o stringhe 'nan'/'none' in None (NULL per Supabase)."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    return s


def _clean_nullable_int(val: Any) -> Optional[int]:
    """Converte un valore numerico opzionale in int (> 0) oppure None."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        num = int(float(s.replace(".", "").replace(",", "")))
        return num if num > 0 else None
    except (ValueError, TypeError):
        return None


def _clean_price_float(val: Any) -> float:
    """
    Converte il campo prezzo (`mio_prezzo`) in float >= 0.0.
    Se `val` è vuoto (`None`, `NaN`, `""`, `"nan"`, `"none"`), restituisce `0.0` senza bloccare.
    Supporta sia la virgola decimale italiana (`64,90` o `1.234,50`) sia il punto (`64.90`).
    """
    if val is None or pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            return round(f, 2) if f >= 0 and not pd.isna(f) else 0.0
        except (ValueError, TypeError):
            return 0.0

    s = str(val).replace("€", "").replace("EUR", "").replace("\xa0", " ").strip()
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


def read_csv_flexible(uploaded_file: Any) -> pd.DataFrame:
    """
    Legge un file CSV accettando automaticamente sia la virgola (`,`) sia il punto e virgola (`;`)
    tipico di Excel in italiano.
    - Usa `pd.read_csv(..., sep=None, engine="python", encoding="utf-8-sig")` con fallback su `encoding="latin1"`.
    - Normalizza i nomi delle colonne (`[c.strip().lower() for c in df.columns]`).
    - Converte qualsiasi valore vuoto, `NaN` o stringa vuota `""` in `None`.
    """
    raw_bytes = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()

    def _try_parse(enc: str, sep_arg: Optional[str] = None) -> pd.DataFrame:
        return pd.read_csv(
            io.BytesIO(raw_bytes),
            sep=sep_arg,
            engine="python",
            encoding=enc,
            dtype=object,
        )

    try:
        df = _try_parse("utf-8-sig", sep_arg=None)
    except Exception:
        try:
            df = _try_parse("latin1", sep_arg=None)
        except Exception:
            df = _try_parse("utf-8-sig", sep_arg=";")

    # Se sep=None non ha separato una riga singola col punto e virgola (es. "keyword;volume_mensile" o "decespugliatori ego;")
    if len(df.columns) == 1 and ";" in str(df.columns[0]):
        try:
            df = _try_parse("utf-8-sig", sep_arg=";")
        except Exception:
            df = _try_parse("latin1", sep_arg=";")
    elif len(df.columns) == 1 and "," in str(df.columns[0]):
        try:
            df = _try_parse("utf-8-sig", sep_arg=",")
        except Exception:
            df = _try_parse("latin1", sep_arg=",")

    df.columns = [str(c).strip().rstrip(";,").strip().lower() for c in df.columns]

    # Converte ogni valore vuoto, NaN o stringa vuota "" in None
    for col in df.columns:
        df[col] = df[col].apply(_clean_nullable_str)

    return df


def _normalize_domain(url_or_domain: str) -> str:
    """Normalizza un dominio rimuovendo protocollo, www. e slash finali per confronti univoci."""
    if not url_or_domain:
        return ""
    raw = str(url_or_domain).strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or parsed.path.split("/")[0]).strip().lower()
    if ":" in host:
        host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")


# =========================================================================
# MODULO PREZZI: PRODOTTI, COMPETITORS, PRODOTTI_COMPETITOR, STORICO_PREZZI
# =========================================================================


def get_prodotti() -> List[Dict[str, Any]]:
    """Restituisce tutti i prodotti ordinati per descrizione."""
    res = execute_with_jwt_retry(
        lambda client: client.table("prodotti").select("*").order("descrizione").execute()
    )
    return res.data if res and res.data else []


def upsert_prodotto(
    sku: Optional[str],
    descrizione: str,
    categoria: str,
    marchio: str,
    mio_prezzo: float,
    mio_url: Optional[str] = None,
) -> Any:
    """
    Inserisce o aggiorna un singolo prodotto.
    Se `sku` è valorizzato esegue upsert su `sku`; se `sku` è None cerca per `descrizione`
    oppure inserisce un nuovo record con `sku = None`.
    """
    sku_clean = _clean_nullable_str(sku)
    url_clean = _clean_nullable_str(mio_url)
    desc_clean = descrizione.strip()

    payload = {
        "sku": sku_clean,
        "descrizione": desc_clean,
        "categoria": categoria.strip() if categoria and categoria.strip() else "Generale",
        "marchio": marchio.strip() if marchio and marchio.strip() else "Generico",
        "mio_prezzo": _clean_price_float(mio_prezzo),
        "mio_url": url_clean,
    }

    if sku_clean is not None:
        return execute_with_jwt_retry(
            lambda client: client.table("prodotti").upsert(payload, on_conflict="sku").execute()
        )

    existing = execute_with_jwt_retry(
        lambda client: client.table("prodotti")
        .select("id")
        .eq("descrizione", desc_clean)
        .limit(1)
        .execute()
    )
    if existing and existing.data:
        existing_id = str(existing.data[0]["id"])
        return execute_with_jwt_retry(
            lambda client: client.table("prodotti").update(payload).eq("id", existing_id).execute()
        )

    return execute_with_jwt_retry(
        lambda client: client.table("prodotti").insert(payload).execute()
    )


def bulk_upsert_prodotti(records: List[Dict[str, Any]]) -> Any:
    """
    Importa/aggiorna una lista di prodotti:
    - Se `sku` è presente: fa upsert sulla colonna `sku`.
    - Se `sku` è None: aggiorna il prodotto con stessa `descrizione` se esiste, altrimenti lo inserisce.
    """
    if not records:
        return None

    with_sku: List[Dict[str, Any]] = []
    without_sku: List[Dict[str, Any]] = []

    for r in records:
        desc_clean = _clean_nullable_str(r.get("descrizione"))
        if not desc_clean:
            continue

        sku_clean = _clean_nullable_str(r.get("sku"))
        url_clean = _clean_nullable_str(r.get("mio_url"))
        cat_clean = _clean_nullable_str(r.get("categoria")) or "Generale"
        mar_clean = _clean_nullable_str(r.get("marchio")) or "Generico"
        prezzo_val = _clean_price_float(r.get("mio_prezzo"))

        item = {
            "sku": sku_clean,
            "descrizione": desc_clean,
            "categoria": cat_clean,
            "marchio": mar_clean,
            "mio_prezzo": prezzo_val,
            "mio_url": url_clean,
        }

        if sku_clean is not None:
            with_sku.append(item)
        else:
            without_sku.append(item)

    if with_sku:
        execute_with_jwt_retry(
            lambda client: client.table("prodotti").upsert(with_sku, on_conflict="sku").execute()
        )

    if without_sku:
        existing_all = get_prodotti()
        desc_to_id = {
            (p.get("descrizione") or "").strip().lower(): str(p["id"])
            for p in existing_all
            if p.get("descrizione")
        }
        to_insert = []
        for item in without_sku:
            key_desc = item["descrizione"].lower()
            if key_desc in desc_to_id:
                target_id = desc_to_id[key_desc]
                execute_with_jwt_retry(
                    lambda client, _it=item, _tid=target_id: client.table("prodotti")
                    .update(_it)
                    .eq("id", _tid)
                    .execute()
                )
            else:
                to_insert.append(item)
        if to_insert:
            execute_with_jwt_retry(
                lambda client: client.table("prodotti").insert(to_insert).execute()
            )

    return True


def update_prodotto(prodotto_id: str, payload: Dict[str, Any]) -> Any:
    """Aggiorna i campi di un prodotto esistente tramite ID (UUID)."""
    cleaned_payload = dict(payload)
    if "sku" in cleaned_payload:
        cleaned_payload["sku"] = _clean_nullable_str(cleaned_payload["sku"])
    if "mio_url" in cleaned_payload:
        cleaned_payload["mio_url"] = _clean_nullable_str(cleaned_payload["mio_url"])
    if "mio_prezzo" in cleaned_payload:
        cleaned_payload["mio_prezzo"] = _clean_price_float(cleaned_payload["mio_prezzo"])
    return execute_with_jwt_retry(
        lambda client: client.table("prodotti")
        .update(cleaned_payload)
        .eq("id", str(prodotto_id))
        .execute()
    )


def delete_prodotto(prodotto_id: str) -> Any:
    """Elimina un singolo prodotto tramite ID (UUID)."""
    return execute_with_jwt_retry(
        lambda client: client.table("prodotti").delete().eq("id", str(prodotto_id)).execute()
    )


def delete_prodotti(prodotto_ids: List[str]) -> Any:
    """Elimina uno o più prodotti tramite lista di ID (UUID)."""
    if not prodotto_ids:
        return None
    str_ids = [str(pid) for pid in prodotto_ids]
    return execute_with_jwt_retry(
        lambda client: client.table("prodotti").delete().in_("id", str_ids).execute()
    )


def get_competitors_prezzi(only_active: bool = True) -> List[Dict[str, Any]]:
    """Restituisce la lista dei competitor (filtrata per attivo_prezzi = True se richiesto)."""
    def _q(client: Client) -> Any:
        query = client.table("competitors").select("*")
        if only_active:
            query = query.eq("attivo_prezzi", True)
        return query.order("nome").execute()

    res = execute_with_jwt_retry(_q)
    return res.data if res and res.data else []


def add_competitor_prezzi(nome: str, dominio: str) -> Any:
    """Inserisce un nuovo competitor attivo sul modulo prezzi (o riattiva se il dominio esiste già)."""
    norm_dom = _normalize_domain(dominio)
    all_comps = execute_with_jwt_retry(
        lambda client: client.table("competitors").select("*").execute()
    )
    for c in (all_comps.data or []):
        if _normalize_domain(c.get("dominio") or "") == norm_dom and norm_dom:
            c_id = str(c["id"])
            return execute_with_jwt_retry(
                lambda client: client.table("competitors")
                .update({"nome": nome.strip(), "dominio": dominio.strip(), "attivo_prezzi": True})
                .eq("id", c_id)
                .execute()
            )

    payload = {
        "nome": nome.strip(),
        "dominio": dominio.strip(),
        "attivo_prezzi": True,
    }
    return execute_with_jwt_retry(
        lambda client: client.table("competitors").insert(payload).execute()
    )


def update_competitor(competitor_id: str, payload: Dict[str, Any]) -> Any:
    """Aggiorna i dati di un competitor esistente tramite ID (UUID)."""
    return execute_with_jwt_retry(
        lambda client: client.table("competitors")
        .update(payload)
        .eq("id", str(competitor_id))
        .execute()
    )


def delete_competitor(competitor_id: str) -> Any:
    """Elimina un competitor tramite ID (UUID)."""
    return execute_with_jwt_retry(
        lambda client: client.table("competitors")
        .delete()
        .eq("id", str(competitor_id))
        .execute()
    )


def get_prodotti_competitor() -> List[Dict[str, Any]]:
    """Restituisce tutte le associazioni prodotto-competitor con URL e prezzi."""
    res = execute_with_jwt_retry(
        lambda client: client.table("prodotti_competitor").select("*").execute()
    )
    return res.data if res and res.data else []


def scollega_prodotto_competitor(prodotto_id: str, competitor_id: str) -> Any:
    """
    Scollega l'URL e azzera completamente `ultimo_prezzo` e `prezzo_precedente`
    per una coppia `(prodotto_id, competitor_id)`.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "prodotto_id": str(prodotto_id),
        "competitor_id": str(competitor_id),
        "url_prodotto": None,
        "ultimo_prezzo": None,
        "prezzo_precedente": None,
        "ultimo_check": now_iso,
    }
    return execute_with_jwt_retry(
        lambda client: client.table("prodotti_competitor")
        .upsert([payload], on_conflict="prodotto_id,competitor_id")
        .execute()
    )


def upsert_prodotti_competitor(
    items: List[Dict[str, Any]],
    record_history: bool = True,
) -> Any:
    """
    Salva/aggiorna i record in 'prodotti_competitor' (upsert su prodotto_id, competitor_id)
    e registra le variazioni di prezzo in 'storico_prezzi'.
    REGOLA AUTOMATICA: Se `url_prodotto` è vuoto (`None` o `""`), imposta automaticamente
    `url_prodotto = None`, `ultimo_prezzo = None` e `prezzo_precedente = None`.
    """
    if not items:
        return None

    now_iso = datetime.now(timezone.utc).isoformat()

    upsert_payload = []
    history_payload = []

    for item in items:
        prod_id = str(item["prodotto_id"])
        comp_id = str(item["competitor_id"])
        url_prod = _clean_nullable_str(item.get("url_prodotto"))
        nuovo_prezzo = item.get("ultimo_prezzo")
        prezzo_prec = item.get("prezzo_precedente")

        if url_prod is None:
            # Regola automatica: se l'URL viene svuotato/eliminato, azzera anche il prezzo e lo storico precedente
            nuovo_prezzo = None
            prezzo_prec = None
        else:
            if nuovo_prezzo is not None:
                try:
                    nuovo_prezzo = round(float(nuovo_prezzo), 2)
                    if nuovo_prezzo <= 0:
                        nuovo_prezzo = None
                except (ValueError, TypeError):
                    nuovo_prezzo = None

            if prezzo_prec is not None:
                try:
                    prezzo_prec = round(float(prezzo_prec), 2)
                    if prezzo_prec <= 0:
                        prezzo_prec = None
                except (ValueError, TypeError):
                    prezzo_prec = None

        row_data: Dict[str, Any] = {
            "prodotto_id": prod_id,
            "competitor_id": comp_id,
            "url_prodotto": url_prod,
            "ultimo_prezzo": nuovo_prezzo,
            "prezzo_precedente": prezzo_prec,
            "ultimo_check": now_iso,
        }
        upsert_payload.append(row_data)

        if record_history and nuovo_prezzo is not None and nuovo_prezzo != prezzo_prec:
            history_payload.append(
                {
                    "prodotto_id": prod_id,
                    "competitor_id": comp_id,
                    "prezzo_rilevato": nuovo_prezzo,
                    "data_rilevazione": now_iso,
                }
            )

    res = execute_with_jwt_retry(
        lambda client: client.table("prodotti_competitor")
        .upsert(upsert_payload, on_conflict="prodotto_id,competitor_id")
        .execute()
    )

    if history_payload:
        try:
            execute_with_jwt_retry(
                lambda client: client.table("storico_prezzi").insert(history_payload).execute()
            )
        except Exception:
            pass

    return res


# =========================================================================
# MODULO SEO: SEO_KEYWORDS, COMPETITORS (SEO), SEO_STORICO
# =========================================================================


def get_seo_keywords() -> List[Dict[str, Any]]:
    """Restituisce tutte le keyword monitorate ordinate alfabeticamente per keyword."""
    res = execute_with_jwt_retry(
        lambda client: client.table("seo_keywords").select("*").order("keyword").execute()
    )
    return res.data if res and res.data else []


def upsert_seo_keyword(keyword: str, volume_mensile: Optional[int] = None) -> Any:
    """
    Inserisce o aggiorna una singola keyword nella tabella 'seo_keywords'.
    Se la keyword esiste già (case-insensitive), aggiorna volume_mensile (se fornito).
    """
    kw_clean = (keyword or "").strip().rstrip(";,").strip()
    if not kw_clean:
        return None

    vol_clean = _clean_nullable_int(volume_mensile)

    existing_all = get_seo_keywords()
    for row in existing_all:
        if (row.get("keyword") or "").strip().lower() == kw_clean.lower():
            row_id = str(row["id"])
            update_data: Dict[str, Any] = {"keyword": kw_clean}
            if vol_clean is not None:
                update_data["volume_mensile"] = vol_clean
            try:
                return execute_with_jwt_retry(
                    lambda client: client.table("seo_keywords")
                    .update(update_data)
                    .eq("id", row_id)
                    .execute()
                )
            except Exception:
                return execute_with_jwt_retry(
                    lambda client: client.table("seo_keywords")
                    .update({"keyword": kw_clean})
                    .eq("id", row_id)
                    .execute()
                )

    payload: Dict[str, Any] = {
        "keyword": kw_clean,
        "volume_mensile": vol_clean,
    }
    try:
        return execute_with_jwt_retry(
            lambda client: client.table("seo_keywords").insert(payload).execute()
        )
    except Exception:
        return execute_with_jwt_retry(
            lambda client: client.table("seo_keywords").insert({"keyword": kw_clean}).execute()
        )


def bulk_upsert_seo_keywords(records: List[Dict[str, Any]]) -> int:
    """
    Importa o aggiorna massivamente una lista di keyword da CSV.
    Restituisce il numero di keyword elaborate.
    """
    if not records:
        return 0

    existing_all = get_seo_keywords()
    kw_map: Dict[str, Dict[str, Any]] = {
        (r.get("keyword") or "").strip().lower(): r
        for r in existing_all
        if r.get("keyword")
    }

    to_insert: List[Dict[str, Any]] = []
    seen_in_batch: set = set()
    count = 0

    for rec in records:
        raw_kw = _clean_nullable_str(rec.get("keyword"))
        if not raw_kw:
            continue
        kw_clean = raw_kw.rstrip(";,").strip()
        if not kw_clean:
            continue
        kw_lower = kw_clean.lower()
        if kw_lower in seen_in_batch:
            continue
        seen_in_batch.add(kw_lower)

        vol_clean = _clean_nullable_int(rec.get("volume_mensile"))
        count += 1

        if kw_lower in kw_map:
            existing_row = kw_map[kw_lower]
            if vol_clean is not None:
                row_id = str(existing_row["id"])
                try:
                    execute_with_jwt_retry(
                        lambda client, _rid=row_id, _v=vol_clean: client.table("seo_keywords")
                        .update({"volume_mensile": _v})
                        .eq("id", _rid)
                        .execute()
                    )
                except Exception:
                    pass
        else:
            to_insert.append(
                {
                    "keyword": kw_clean,
                    "volume_mensile": vol_clean,
                }
            )

    if to_insert:
        try:
            execute_with_jwt_retry(
                lambda client: client.table("seo_keywords").insert(to_insert).execute()
            )
        except Exception:
            to_insert_no_vol = [{"keyword": r["keyword"]} for r in to_insert]
            execute_with_jwt_retry(
                lambda client: client.table("seo_keywords").insert(to_insert_no_vol).execute()
            )

    return count


def delete_seo_keyword(keyword_id: str) -> Any:
    """Elimina una singola keyword tramite ID (UUID) e il relativo storico."""
    kw_id_str = str(keyword_id)
    try:
        execute_with_jwt_retry(
            lambda client: client.table("seo_storico")
            .delete()
            .eq("keyword_id", kw_id_str)
            .execute()
        )
    except Exception:
        pass
    return execute_with_jwt_retry(
        lambda client: client.table("seo_keywords").delete().eq("id", kw_id_str).execute()
    )


def get_competitors_seo(only_active: bool = True) -> List[Dict[str, Any]]:
    """Restituisce la lista dei competitor (filtrata per attivo_seo = True se richiesto)."""
    def _q(client: Client) -> Any:
        query = client.table("competitors").select("*")
        if only_active:
            query = query.eq("attivo_seo", True)
        return query.order("nome").execute()

    res = execute_with_jwt_retry(_q)
    return res.data if res and res.data else []


def add_competitor_seo(nome: str, dominio: str) -> Any:
    """
    Aggiunge un nuovo competitor con `attivo_seo = True`, oppure se il dominio
    esiste già nella tabella `competitors`, attiva il flag `attivo_seo = True`.
    """
    norm_dom = _normalize_domain(dominio)
    all_comps = execute_with_jwt_retry(
        lambda client: client.table("competitors").select("*").execute()
    )
    for c in (all_comps.data or []):
        if _normalize_domain(c.get("dominio") or "") == norm_dom and norm_dom:
            c_id = str(c["id"])
            update_payload: Dict[str, Any] = {"attivo_seo": True}
            if nome.strip():
                update_payload["nome"] = nome.strip()
            if dominio.strip():
                update_payload["dominio"] = dominio.strip()
            return execute_with_jwt_retry(
                lambda client: client.table("competitors")
                .update(update_payload)
                .eq("id", c_id)
                .execute()
            )

    payload = {
        "nome": nome.strip(),
        "dominio": dominio.strip(),
        "attivo_seo": True,
    }
    return execute_with_jwt_retry(
        lambda client: client.table("competitors").insert(payload).execute()
    )


def salva_rilevazione_serp_keyword(
    keyword_row: Dict[str, Any],
    nuova_mia_posizione: Optional[int],
    mio_url_posizionato: Optional[str],
    risultati_competitors: List[Dict[str, Any]],
    volume_stimato: Optional[int] = None,
    top_5_serp: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """
    Salva i risultati di una rilevazione SERP per una singola keyword:
    1. Sposta `mia_posizione_attuale` in `mia_posizione_precedente` (se non era NULL,
       altrimenti imposta `mia_posizione_precedente = nuova_mia_posizione` così la variazione iniziale è 0).
    2. Aggiorna `mia_posizione_attuale`, `volume_mensile` (se stimato), `top_5_serp` e `ultimo_check = NOW()`.
    3. Inserisce i record in `seo_storico` sia per il nostro sito (`is_mio_sito = True`, `competitor_id = None`)
       sia per ogni competitor SEO attivo (`is_mio_sito = False`, `competitor_id = comp_id`).
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    kw_id = str(keyword_row["id"])
    old_pos_raw = keyword_row.get("mia_posizione_attuale")
    old_pos = int(old_pos_raw) if old_pos_raw is not None and not pd.isna(old_pos_raw) else None

    new_pos = (
        int(nuova_mia_posizione)
        if nuova_mia_posizione is not None and 1 <= int(nuova_mia_posizione) <= 100
        else None
    )

    # Regola 1: se mia_posizione_attuale non era NULL la sposta in mia_posizione_precedente,
    # altrimenti imposta mia_posizione_precedente = new_pos (variazione iniziale = 0)
    if old_pos is not None:
        pos_precedente = old_pos
    else:
        pos_precedente = new_pos

    kw_update: Dict[str, Any] = {
        "mia_posizione_attuale": new_pos,
        "mia_posizione_precedente": pos_precedente,
        "ultimo_check": now_iso,
    }

    if top_5_serp is not None:
        kw_update["top_5_serp"] = top_5_serp

    # Preserva sempre il volume_mensile già presente (> 0); usa volume_stimato solo se era vuoto
    current_vol = _clean_nullable_int(keyword_row.get("volume_mensile"))
    new_vol = _clean_nullable_int(volume_stimato)
    if current_vol is not None:
        kw_update["volume_mensile"] = current_vol
    elif new_vol is not None:
        kw_update["volume_mensile"] = new_vol

    try:
        execute_with_jwt_retry(
            lambda client: client.table("seo_keywords").update(kw_update).eq("id", kw_id).execute()
        )
    except Exception:
        try:
            kw_update_no_top5 = {k: v for k, v in kw_update.items() if k != "top_5_serp"}
            execute_with_jwt_retry(
                lambda client: client.table("seo_keywords")
                .update(kw_update_no_top5)
                .eq("id", kw_id)
                .execute()
            )
        except Exception:
            kw_update_base = {
                k: v
                for k, v in kw_update.items()
                if k not in ("volume_mensile", "top_5_serp")
            }
            execute_with_jwt_retry(
                lambda client: client.table("seo_keywords")
                .update(kw_update_base)
                .eq("id", kw_id)
                .execute()
            )

    # Regola 3: inserimento in seo_storico (nostro sito + tutti i competitor SEO attivi)
    storico_rows: List[Dict[str, Any]] = [
        {
            "keyword_id": kw_id,
            "competitor_id": None,
            "is_mio_sito": True,
            "posizione": new_pos,
            "url_posizionato": _clean_nullable_str(mio_url_posizionato),
            "data_rilevazione": now_iso,
        }
    ]

    for comp_res in risultati_competitors:
        c_id = comp_res.get("competitor_id")
        if not c_id:
            continue
        c_pos_raw = comp_res.get("posizione")
        c_pos = (
            int(c_pos_raw)
            if c_pos_raw is not None and 1 <= int(c_pos_raw) <= 100
            else None
        )
        storico_rows.append(
            {
                "keyword_id": kw_id,
                "competitor_id": str(c_id),
                "is_mio_sito": False,
                "posizione": c_pos,
                "url_posizionato": _clean_nullable_str(comp_res.get("url_posizionato")),
                "data_rilevazione": now_iso,
            }
        )

    try:
        execute_with_jwt_retry(
            lambda client: client.table("seo_storico").insert(storico_rows).execute()
        )
    except Exception:
        # Fallback nel caso in cui la colonna data_rilevazione usi il default del DB
        storico_rows_no_ts = [
            {k: v for k, v in r.items() if k != "data_rilevazione"}
            for r in storico_rows
        ]
        try:
            execute_with_jwt_retry(
                lambda client: client.table("seo_storico").insert(storico_rows_no_ts).execute()
            )
        except Exception:
            pass

    return True


def get_seo_storico(keyword_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Restituisce i record della tabella `seo_storico` (filtrati per `keyword_id` se passato),
    ordinati cronologicamente dal meno recente al più recente.
    """
    def _q(client: Client) -> Any:
        query = client.table("seo_storico").select("*")
        if keyword_id:
            query = query.eq("keyword_id", str(keyword_id))
        return query.execute()

    res = execute_with_jwt_retry(_q)
    rows = res.data if res and res.data else []
    rows.sort(
        key=lambda r: str(
            r.get("data_rilevazione") or r.get("created_at") or r.get("id") or ""
        )
    )
    return rows


def get_ultime_posizioni_serp() -> Dict[str, Dict[str, Any]]:
    """
    Costruisce una mappa veloce a partire da `seo_storico` con l'ultima rilevazione
    per ogni `keyword_id`:
    {
        keyword_id: {
            "mio": {"posizione": int|None, "url_posizionato": str|None, "data": str|None},
            "competitors": {
                comp_id: {"posizione": int|None, "url_posizionato": str|None, "data": str|None}
            }
        }
    }
    """
    all_rows = get_seo_storico()
    latest_map: Dict[str, Dict[str, Any]] = {}

    for r in all_rows:
        kw_id = str(r.get("keyword_id") or "")
        if not kw_id:
            continue
        if kw_id not in latest_map:
            latest_map[kw_id] = {"mio": {}, "competitors": {}}

        ts = r.get("data_rilevazione") or r.get("created_at")
        entry = {
            "posizione": r.get("posizione"),
            "url_posizionato": r.get("url_posizionato"),
            "data": ts,
        }

        if r.get("is_mio_sito"):
            latest_map[kw_id]["mio"] = entry
        elif r.get("competitor_id"):
            latest_map[kw_id]["competitors"][str(r["competitor_id"])] = entry

    return latest_map


# =========================================================================
# MODULO BACKLINK: TABELLA 'BACKLINKS'
# =========================================================================


def _normalize_url_input(url_val: Optional[str]) -> Optional[str]:
    """Assicura che un URL valido inizi con http:// o https://."""
    cleaned = _clean_nullable_str(url_val)
    if not cleaned:
        return None
    cleaned = cleaned.rstrip(";,").strip()
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    return cleaned


def get_backlinks() -> List[Dict[str, Any]]:
    """Restituisce tutti i backlink ordinati per created_at DESC."""
    res = execute_with_jwt_retry(
        lambda client: client.table("backlinks")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data if res and res.data else []


def add_backlink(
    url_sorgente: str,
    url_destinazione: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Inserisce un singolo backlink nella tabella 'backlinks'.
    Se `url_destinazione` è lasciato vuoto dall'utente, usa il `sito_web` dalle `impostazioni`
    come riferimento del nostro dominio.
    """
    src_clean = _normalize_url_input(url_sorgente)
    if not src_clean:
        return None

    dest_clean = _normalize_url_input(url_destinazione)
    if not dest_clean:
        impostazioni = get_impostazioni()
        dest_clean = _normalize_url_input(impostazioni.get("sito_web")) or ""

    existing_all = get_backlinks()
    for row in existing_all:
        if (row.get("url_sorgente") or "").strip().lower() == src_clean.lower():
            if dest_clean:
                row_id = str(row["id"])
                execute_with_jwt_retry(
                    lambda client: client.table("backlinks")
                    .update({"url_destinazione": dest_clean})
                    .eq("id", row_id)
                    .execute()
                )
            return row

    payload: Dict[str, Any] = {
        "url_sorgente": src_clean,
        "url_destinazione": dest_clean,
        "stato_attivo": False,
    }
    res = execute_with_jwt_retry(
        lambda client: client.table("backlinks").insert(payload).execute()
    )
    return res.data[0] if res and res.data else payload


def bulk_insert_backlinks(records: List[Dict[str, Any]]) -> int:
    """
    Inserisce massivamente una lista di backlink da CSV (`url_sorgente` obbligatorio,
    `url_destinazione` opzionale). Restituisce il numero di backlink elaborati.
    """
    if not records:
        return 0

    impostazioni = get_impostazioni()
    default_dest = _normalize_url_input(impostazioni.get("sito_web")) or ""

    existing_all = get_backlinks()
    src_map: Dict[str, Dict[str, Any]] = {
        (r.get("url_sorgente") or "").strip().lower(): r
        for r in existing_all
        if r.get("url_sorgente")
    }

    to_insert: List[Dict[str, Any]] = []
    seen_in_batch: set = set()
    count = 0

    for rec in records:
        src_clean = _normalize_url_input(rec.get("url_sorgente"))
        if not src_clean:
            continue
        src_lower = src_clean.lower()
        if src_lower in seen_in_batch:
            continue
        seen_in_batch.add(src_lower)

        dest_clean = _normalize_url_input(rec.get("url_destinazione")) or default_dest
        count += 1

        if src_lower in src_map:
            existing_row = src_map[src_lower]
            if dest_clean and dest_clean != existing_row.get("url_destinazione"):
                row_id = str(existing_row["id"])
                execute_with_jwt_retry(
                    lambda client, _rid=row_id, _d=dest_clean: client.table("backlinks")
                    .update({"url_destinazione": _d})
                    .eq("id", _rid)
                    .execute()
                )
        else:
            to_insert.append(
                {
                    "url_sorgente": src_clean,
                    "url_destinazione": dest_clean,
                    "stato_attivo": False,
                }
            )

    if to_insert:
        execute_with_jwt_retry(
            lambda client: client.table("backlinks").insert(to_insert).execute()
        )

    return count


def update_backlink(backlink_id: str, payload: Dict[str, Any]) -> Any:
    """Modifica i dati di un backlink esistente tramite ID (UUID)."""
    cleaned = dict(payload)
    if "url_sorgente" in cleaned:
        cleaned["url_sorgente"] = _normalize_url_input(cleaned["url_sorgente"])
    if "url_destinazione" in cleaned:
        impostazioni = get_impostazioni()
        default_dest = _normalize_url_input(impostazioni.get("sito_web")) or ""
        cleaned["url_destinazione"] = (
            _normalize_url_input(cleaned["url_destinazione"]) or default_dest
        )
    return execute_with_jwt_retry(
        lambda client: client.table("backlinks")
        .update(cleaned)
        .eq("id", str(backlink_id))
        .execute()
    )


def delete_backlink(backlink_id: str) -> Any:
    """Elimina un singolo backlink tramite ID (UUID)."""
    return execute_with_jwt_retry(
        lambda client: client.table("backlinks")
        .delete()
        .eq("id", str(backlink_id))
        .execute()
    )


def salva_esito_backlink(
    backlink_id: str,
    stato_attivo: bool,
    http_status: Optional[int],
    rel_attribute: Optional[str],
    anchor_text: Optional[str],
    url_destinazione_trovato: Optional[str] = None,
) -> Any:
    """
    Salva l'esito della verifica di un singolo backlink:
    `stato_attivo`, `http_status`, `rel_attribute`, `anchor_text`, `ultimo_check = NOW()`
    (e aggiorna `url_destinazione` con l'href effettivo se rilevato).
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    update_data: Dict[str, Any] = {
        "stato_attivo": bool(stato_attivo),
        "http_status": int(http_status) if http_status is not None else 0,
        "rel_attribute": _clean_nullable_str(rel_attribute),
        "anchor_text": _clean_nullable_str(anchor_text),
        "ultimo_check": now_iso,
    }
    if url_destinazione_trovato and str(url_destinazione_trovato).strip():
        update_data["url_destinazione"] = str(url_destinazione_trovato).strip()

    return execute_with_jwt_retry(
        lambda client: client.table("backlinks")
        .update(update_data)
        .eq("id", str(backlink_id))
        .execute()
    )

