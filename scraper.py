import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

# Carica subito le variabili d'ambiente aggiornate (.env)
load_dotenv(override=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}

GEMINI_MODEL_PRIMARY = "gemini-3.8-flash"
GEMINI_MODEL_FALLBACK = "gemini-2.5-flash"

_DOMAIN_WORKING_SEARCH_TEMPLATE: Dict[str, str] = {}


def clean_domain(url: str) -> str:
    """
    Usa urllib.parse.urlparse per estrarre solo l'host pulito senza
    http://, https://, www. e senza slash finali (es. 'puntoirrigazione.it').
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or parsed.path).lower().strip().strip("/")
    if host.startswith("www."):
        host = host[4:]
    return host


def _fetch_html_with_url(url: str, timeout: int = 5) -> Tuple[Optional[str], str]:
    """
    Scarica l'HTML usando curl_cffi con impersonate='chrome120' e timeout=5.
    Se curl_cffi non è installato usa requests con timeout=(3, 5).
    """
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        return None, target

    if cffi_requests is not None:
        try:
            resp = cffi_requests.get(
                target,
                impersonate="chrome120",
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            if resp.status_code < 400 and resp.text:
                return resp.text, str(resp.url or target)
            return None, target
        except Exception:
            return None, target

    try:
        resp_std = requests.get(
            target,
            headers=HEADERS,
            timeout=(3, timeout),
            allow_redirects=True,
        )
        if resp_std.status_code < 400 and resp_std.text:
            return resp_std.text, str(resp_std.url or target)
    except Exception:
        pass

    return None, target


def fetch_html(url: str, timeout: int = 5) -> Optional[str]:
    """
    Scarica l'HTML di una pagina usando curl_cffi (impersonate='chrome120', timeout=5).
    """
    html, _ = _fetch_html_with_url(url, timeout=timeout)
    return html


def _get_secret_safe(key: str) -> str:
    """
    Legge una chiave da `os.getenv(key)` (popolata da `.env` o GitHub Actions) con fallback
    sicuro su `st.secrets[key]` (solo se nel main thread di Streamlit) e sincronizzazione in `os.environ`.
    """
    val = os.getenv(key, "").strip()
    if val:
        return val
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        import streamlit as st

        if get_script_run_ctx() is not None and key in st.secrets:
            s_val = str(st.secrets[key]).strip()
            if s_val:
                os.environ[key] = s_val
                return s_val
    except Exception:
        pass
    return ""


def _get_gemini_api_key() -> Optional[str]:
    key = _get_secret_safe("GEMINI_API_KEY")
    if not key or "inserisci" in key.lower():
        return None
    return key


def _get_serper_api_key() -> Optional[str]:
    key = _get_secret_safe("SERPER_API_KEY")
    if not key or "inserisci" in key.lower():
        return None
    return key


def _call_gemini_text(prompt: str) -> Optional[str]:
    """
    Chiama Gemini tramite google-genai SDK con hard timeout di 6 secondi (http_options={'timeout': 6000}).
    """
    api_key = _get_gemini_api_key()
    if not api_key:
        return None
    try:
        from google import genai

        try:
            client = genai.Client(api_key=api_key, http_options={"timeout": 6000})
        except Exception:
            client = genai.Client(api_key=api_key)

        for model_name in (GEMINI_MODEL_PRIMARY, GEMINI_MODEL_FALLBACK):
            try:
                if hasattr(client, "interactions"):
                    interaction = client.interactions.create(
                        model=model_name,
                        input=prompt,
                        timeout=6.0,
                    )
                    out = getattr(interaction, "output_text", None)
                    if out:
                        return out
                else:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    out = getattr(response, "text", None)
                    if out:
                        return out
            except Exception:
                continue
    except Exception:
        return None
    return None


def _is_blocked_or_captcha_html(html: str, clean_text: str) -> bool:
    """Rileva pagine di blocco Cloudflare, Captcha Amazon o pagine troppo corte (< 500 char)."""
    if not html or not clean_text or len(clean_text) < 500:
        return True
    lower_txt = clean_text.lower()
    lower_html = html[:4000].lower()
    block_markers = (
        "just a moment...",
        "enable javascript and cookies to continue",
        "cf-browser-verification",
        "challenge-platform",
        "inserisci i caratteri visualizzati nello spazio sottostante",
        "digita i caratteri che vedi nell'immagine",
        "automated access to amazon data",
        " access denied ",
    )
    return any(m in lower_txt or m in lower_html for m in block_markers)


def _normalize_amazon_url(url: str) -> str:
    """Pulisce un URL Amazon mantenendo il formato canonico con /dp/ASIN o /gp/product/ASIN."""
    m = re.search(
        r"(https?://[^/]*amazon\.[a-z.]+/(?:[^/]+/)?(?:dp|gp/product)/[A-Z0-9]{10})",
        url,
        re.I,
    )
    if m:
        return m.group(1)
    return url.split("?")[0] if "/dp/" in url or "/gp/" in url else url


def _is_likely_product_url(url: str, dom_clean: str) -> bool:
    """Verifica che l'URL appartenga al dominio e sia una scheda prodotto valida."""
    if not url or not dom_clean:
        return False
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if dom_clean not in host:
            return False
        path = (parsed.path or "").strip("/")
        if not path:
            return False

        lower_url = url.lower()

        # Regola specifica per Amazon: deve contenere /dp/ o /gp/
        if "amazon." in dom_clean:
            if "/dp/" not in lower_url and "/gp/" not in lower_url:
                return False
            if "/gp/aw/s" in lower_url or "/s?" in lower_url:
                return False
            return True

        if lower_url.endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".pdf", ".webp", ".svg", ".css", ".js")
        ):
            return False

        excluded_substrings = (
            "/cart",
            "/carrello",
            "/checkout",
            "/login",
            "/account",
            "/contatti",
            "/contact",
            "/privacy",
            "/cookie",
            "/blog/",
            "/tag/",
            "controller=search",
            "?s=",
            "search=true",
            "/component/virtuemart/by,",
            "/component/virtuemart/dirdesc",
            "/component/virtuemart/dirasc",
            "?orderby=",
            "results,",
            "/catalogsearch/",
        )
        if any(seg in lower_url for seg in excluded_substrings):
            return False
        return True
    except Exception:
        return False


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _score_candidate_link(
    url: str,
    title: str,
    descrizione: str,
    marchio: Optional[str] = None,
    sku: Optional[str] = None,
) -> int:
    """
    Calcola uno score di pertinenza tra un link candidato e il prodotto cercato.
    Controlla rigorosamente 'interno' vs 'esterno', modello e numero stazioni/zone.
    """
    desc_tokens = _tokenize(descrizione)
    if not desc_tokens:
        return 0
    desc_token_set = set(desc_tokens)

    slug_text = urlparse(url).path.replace("-", " ").replace("/", " ").replace("_", " ")
    cand_text = f"{title} {slug_text}".lower()
    cand_tokens = set(_tokenize(cand_text))
    cand_compact = re.sub(r"[^a-z0-9]", "", cand_text)

    # --- Distinzione rigorosa INTERNO vs ESTERNO ---
    indoor_words = {"interno", "interni", "indoor"}
    outdoor_words = {"esterno", "esterni", "outdoor"}

    prod_is_indoor = bool(desc_token_set & indoor_words)
    prod_is_outdoor = bool(desc_token_set & outdoor_words)

    cand_is_indoor = bool(cand_tokens & indoor_words) or any(
        w in cand_compact for w in indoor_words
    )
    cand_is_outdoor = bool(cand_tokens & outdoor_words) or any(
        w in cand_compact for w in outdoor_words
    )

    if prod_is_indoor and not prod_is_outdoor:
        if cand_is_outdoor and not cand_is_indoor:
            return 0
    elif prod_is_outdoor and not prod_is_indoor:
        if cand_is_indoor and not cand_is_outdoor:
            return 0

    lower_url = url.lower()
    score = 0

    if lower_url.endswith("-detail") or lower_url.endswith(".html") or "/dp/" in lower_url:
        score += 15

    if prod_is_indoor and cand_is_indoor:
        score += 25
    elif prod_is_outdoor and cand_is_outdoor:
        score += 25

    if "kit" in cand_tokens and "kit" not in desc_token_set:
        score -= 40

    stopwords = {
        "da",
        "di",
        "per",
        "con",
        "in",
        "il",
        "la",
        "lo",
        "le",
        "gli",
        "un",
        "una",
        "stazioni",
        "stazione",
        "zone",
        "zona",
        "centralina",
        "programmatore",
        "interno",
        "interni",
        "indoor",
        "esterno",
        "esterni",
        "outdoor",
        "wifi",
        "wi",
        "fi",
    }

    brand_tokens = set(_tokenize(marchio or ""))
    model_tokens = [
        t
        for t in desc_tokens
        if t not in stopwords and t not in brand_tokens and not t.isdigit()
    ]

    if model_tokens:
        matched_models = sum(
            1
            for mt in model_tokens
            if mt in cand_tokens or (len(mt) >= 3 and mt in cand_compact)
        )
        if matched_models == 0:
            return 0
        score += matched_models * 30

    if brand_tokens:
        brand_compact = "".join(_tokenize(marchio or ""))
        if any(bt in cand_tokens for bt in brand_tokens) or (
            len(brand_compact) >= 4 and brand_compact in cand_compact
        ):
            score += 15

    for tok in desc_tokens:
        if tok.isdigit():
            if tok in cand_tokens:
                score += 30
            else:
                score -= 25

    if sku and len(sku.strip()) >= 4:
        sku_tok = sku.strip().lower()
        if sku_tok in cand_text:
            score += 30

    return max(score, 0)


def _search_serper_google(
    dom_clean: str,
    descrizione: str,
    marchio: Optional[str] = None,
    sku: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Priorità Assoluta: Google Search tramite Serper.dev (https://google.serper.dev/search)
    con timeout=(3, 5).
    """
    serper_key = _get_serper_api_key()
    if not serper_key:
        return []

    mar = (marchio or "").strip()
    if mar.lower() in ("generico", "non specificato", "—"):
        mar = ""
    desc = (descrizione or "").strip()

    if mar and mar.lower() not in desc.lower():
        q_primary = f"site:{dom_clean} {mar} {desc}".strip()
    else:
        q_primary = f"site:{dom_clean} {desc}".strip()

    serper_queries = [q_primary]

    if mar:
        pattern = re.compile(re.escape(mar), re.IGNORECASE)
        no_brand = pattern.sub("", desc).strip()
        if no_brand:
            q_nobrand = f"site:{dom_clean} {no_brand}".strip()
            if q_nobrand not in serper_queries:
                serper_queries.append(q_nobrand)

    candidates: List[Dict[str, Any]] = []
    seen = set()

    for q in serper_queries:
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": serper_key,
                    "Content-Type": "application/json",
                },
                json={"q": q, "gl": "it", "hl": "it", "num": 5},
                timeout=(3, 5),
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                continue
            data = resp.json()
            organic = data.get("organic", []) or []

            for item in organic:
                raw_link = (item.get("link") or "").strip()
                if not raw_link:
                    continue
                if "amazon." in dom_clean:
                    raw_link = _normalize_amazon_url(raw_link)

                if raw_link in seen or not _is_likely_product_url(raw_link, dom_clean):
                    continue

                title = (item.get("title") or "").strip()
                snippet = (item.get("snippet") or "").strip()
                sc = _score_candidate_link(
                    raw_link, f"{title} {snippet}", descrizione, marchio, sku
                )
                if sc > 0:
                    seen.add(raw_link)
                    candidates.append(
                        {
                            "url": raw_link,
                            "title": title,
                            "body": snippet,
                            "score": sc,
                        }
                    )
            if candidates:
                break
        except Exception:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def _search_internal_ecommerce_fast(
    dom_clean: str,
    descrizione: str,
    marchio: Optional[str] = None,
    sku: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Usato SOLO se SERPER_API_KEY non è configurata nel .env:
    ricerca rapida (max 2 richieste con timeout=5) nel motore interno dell'e-commerce.
    """
    if "amazon." in dom_clean:
        endpoints = [f"https://www.{dom_clean}/s?k={{q}}"]
    elif "puntoirrigazione." in dom_clean:
        endpoints = [
            f"https://{dom_clean}/component/virtuemart/?search=true&limitstart=0&option=com_virtuemart&view=category&keyword={{q}}"
        ]
    else:
        endpoints = [
            f"https://www.{dom_clean}/ricerca?controller=search&s={{q}}",
            f"https://www.{dom_clean}/?s={{q}}&post_type=product",
        ]

    if dom_clean in _DOMAIN_WORKING_SEARCH_TEMPLATE:
        best_ep = _DOMAIN_WORKING_SEARCH_TEMPLATE[dom_clean]
        endpoints = [best_ep]

    q_clean = " ".join(
        [
            w
            for w in (descrizione or "").split()
            if w.lower() not in ("stazioni", "stazione", "zone", "zona")
        ]
    ).strip()
    if not q_clean:
        return []

    candidates_by_url: Dict[str, Dict[str, Any]] = {}
    product_selectors = (
        ".product-title a, .product-name a, a.product-name, "
        "h2.woocommerce-loop-product__title a, article.product-miniature a.thumbnail, "
        ".vm-product-descr-container-1 h2 a, a[href$='-detail'], "
        "div[data-component-type='s-search-result'] h2 a"
    )

    for ep_template in endpoints[:2]:
        search_url = ep_template.format(q=quote_plus(q_clean))
        html, final_url = _fetch_html_with_url(search_url, timeout=5)
        if not html:
            continue
        _DOMAIN_WORKING_SEARCH_TEMPLATE[dom_clean] = ep_template

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        links = soup.select(product_selectors) or soup.find_all("a", href=True)
        for a in links:
            raw_href = (a.get("href") or "").strip()
            if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full_url = urljoin(final_url, raw_href)
            if "amazon." in dom_clean:
                full_url = _normalize_amazon_url(full_url)
            if not _is_likely_product_url(full_url, dom_clean):
                continue

            title_text = a.get_text(" ", strip=True) or (a.get("title") or "").strip()
            sc = _score_candidate_link(full_url, title_text, descrizione, marchio, sku)
            if sc > 0:
                prev = candidates_by_url.get(full_url)
                if not prev or sc > prev["score"]:
                    candidates_by_url[full_url] = {
                        "url": full_url,
                        "title": title_text or full_url,
                        "body": "",
                        "score": sc,
                    }
        if candidates_by_url:
            break

    sorted_cands = sorted(
        candidates_by_url.values(), key=lambda x: x["score"], reverse=True
    )
    return sorted_cands[:5]


def trova_url_prodotto(
    dominio: str,
    sku: Optional[str],
    descrizione: str,
    marchio: Optional[str] = None,
) -> Optional[str]:
    """
    Auto-Discovery veloce con hard-timeout:
    - Se SERPER_API_KEY è configurata nel .env: interroga esclusivamente Google Search (Serper.dev).
      Se Serper restituisce 0 risultati pertinenti, restituisce subito None senza fare scraping lento.
    - Se SERPER_API_KEY non è configurata: fa un singolo tentativo rapido sulla ricerca interna del sito.
    """
    dom_clean = clean_domain(dominio)
    if not dom_clean or not (descrizione or sku):
        return None

    if _get_serper_api_key():
        candidates = _search_serper_google(dom_clean, descrizione, marchio, sku)
        if not candidates:
            return None
    else:
        candidates = _search_internal_ecommerce_fast(dom_clean, descrizione, marchio, sku)
        if not candidates:
            return None

    # Se ci sono più candidati con punteggio ravvicinato e GEMINI_API_KEY è attiva, chiedi conferma a Gemini
    if (
        _get_gemini_api_key()
        and len(candidates) > 1
        and (candidates[0]["score"] - candidates[1]["score"] < 20)
    ):
        cand_text = "\n".join(
            [
                f"{idx + 1}. URL: {c['url']} | Titolo: {c['title']} | Snippet: {c.get('body', '')}"
                for idx, c in enumerate(candidates[:5])
            ]
        )
        prompt = (
            f"Devi individuare la pagina scheda prodotto esatta sul sito {dom_clean} "
            f"per questo articolo:\n"
            f"- Descrizione: {descrizione}\n"
            f"- Marchio: {marchio or 'N/D'}\n"
            f"- Codice/SKU: {sku or 'N/D'}\n\n"
            f"Attenzione: rispetta rigorosamente la distinzione tra versione 'interno' (indoor) "
            f"ed 'esterno' (outdoor) e il numero di stazioni/zone.\n\n"
            f"Risultati candidati:\n{cand_text}\n\n"
            f"Rispondi ESCLUSIVAMENTE con l'URL esatto scelto tra quelli elencati sopra. "
            f"Se nessuno è pertinente, rispondi con NONE."
        )
        ai_reply = _call_gemini_text(prompt)
        if ai_reply:
            cleaned_reply = ai_reply.strip().split()[0].strip("`'\".,;")
            for c in candidates:
                if c["url"] == cleaned_reply or cleaned_reply in c["url"]:
                    return c["url"]

    return candidates[0]["url"]


def _parse_price_string(val: Any) -> Optional[float]:
    """
    Converte una stringa o numero di prezzo in float positivo.
    Supporta formato italiano/europeo ('1.234,50 €', '64,90 €') e internazionale ('64.90').
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        fval = float(val)
        return round(fval, 2) if fval > 0 else None

    s = str(val).strip()
    if not s:
        return None

    match = re.search(r"\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?", s.replace("\xa0", " "))
    if not match:
        return None

    s_clean = match.group(0)

    if "," in s_clean and "." in s_clean:
        if s_clean.rfind(",") > s_clean.rfind("."):
            s_clean = s_clean.replace(".", "").replace(",", ".")
        else:
            s_clean = s_clean.replace(",", "")
    elif "," in s_clean:
        parts = s_clean.split(",")
        if len(parts[-1]) in (1, 2):
            s_clean = s_clean.replace(",", ".")
        else:
            s_clean = s_clean.replace(",", "")

    try:
        num = float(s_clean)
        return round(num, 2) if 0 < num < 1000000 else None
    except ValueError:
        return None


def _extract_price_from_jsonld(data: Any) -> Optional[float]:
    """Esplora ricorsivamente un oggetto JSON-LD alla ricerca di Product -> offers -> price."""
    if isinstance(data, list):
        for item in data:
            found = _extract_price_from_jsonld(item)
            if found is not None:
                return found
        return None

    if not isinstance(data, dict):
        return None

    obj_type = str(data.get("@type", "")).lower()
    if "product" in obj_type or "offer" in obj_type:
        for key in ("price", "lowPrice", "highPrice"):
            if key in data:
                p = _parse_price_string(data[key])
                if p is not None:
                    return p
        offers = data.get("offers")
        if offers:
            found = _extract_price_from_jsonld(offers)
            if found is not None:
                return found

    if "@graph" in data:
        found = _extract_price_from_jsonld(data["@graph"])
        if found is not None:
            return found

    return None


def estrai_prezzo_da_url(url: str, descrizione: str = "") -> Optional[float]:
    """
    Scarica la pagina prodotto con curl_cffi (impersonate='chrome120', timeout=5) ed estrae il prezzo:
    1. Selettori Amazon.it (span.a-price span.a-offscreen, .a-price-whole + .a-price-fraction).
    2. JSON-LD (schema.org/Product -> offers).
    3. Meta Tag (product:price:amount, og:price:amount, itemprop="price").
    4. Selettori DOM VirtueMart (.PricesalesPrice, .price-sales), PrestaShop (.current-price, [itemprop="price"]), WooCommerce.
    5. Regex Euro sul blocco prodotto.
    6. Fallback finale AI con Gemini (timeout 6s) SOLO se la pagina è valida (> 500 char e non bloccata da Cloudflare/Captcha).
    """
    if not url or not str(url).strip().startswith(("http://", "https://")):
        return None

    target_url = str(url).strip()
    html = fetch_html(target_url, timeout=5)
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # 1. Supporto specifico Amazon.it
    if "amazon." in target_url.lower():
        amazon_selectors = [
            "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
            "#corePrice_feature_div span.a-price span.a-offscreen",
            "#apex_desktop span.a-price span.a-offscreen",
            "span.a-price.aok-align-center span.a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#price_inside_buybox",
            "#newBuyBoxPrice",
            "span.a-price span.a-offscreen",
        ]
        for sel in amazon_selectors:
            el = soup.select_one(sel)
            if el:
                p = _parse_price_string(el.get_text(" ", strip=True))
                if p is not None:
                    return p

        whole_el = soup.select_one(
            "#corePriceDisplay_desktop_feature_div .a-price-whole, #corePrice_feature_div .a-price-whole, .a-price-whole"
        )
        frac_el = soup.select_one(
            "#corePriceDisplay_desktop_feature_div .a-price-fraction, #corePrice_feature_div .a-price-fraction, .a-price-fraction"
        )
        if whole_el:
            w_txt = re.sub(r"[^\d]", "", whole_el.get_text())
            f_txt = re.sub(r"[^\d]", "", frac_el.get_text()) if frac_el else "00"
            p = _parse_price_string(f"{w_txt}.{f_txt}")
            if p is not None:
                return p

    # 2. JSON-LD (schema.org/Product -> offers)
    for script in soup.find_all("script", type="application/ld+json"):
        raw_json = script.string or script.get_text()
        if not raw_json:
            continue
        try:
            parsed_json = json.loads(raw_json.strip())
            price = _extract_price_from_jsonld(parsed_json)
            if price is not None:
                return price
        except Exception:
            continue

    # 3. Meta Tag OpenGraph / Product / Microdata
    meta_selectors = [
        ("meta", {"property": "product:price:amount"}),
        ("meta", {"property": "og:price:amount"}),
        ("meta", {"name": "product:price:amount"}),
        ("meta", {"itemprop": "price"}),
    ]
    for tag_name, attrs in meta_selectors:
        el = soup.find(tag_name, attrs=attrs)
        if el and el.get("content"):
            price = _parse_price_string(el.get("content"))
            if price is not None:
                return price

    # 4. Selettori DOM VirtueMart, PrestaShop (Agricolbio/Mondoirrigazione), WooCommerce, Magento
    dom_selectors = [
        '.current-price span[itemprop="price"]',
        ".current-price-value",
        ".current-price",
        '[itemprop="price"]',
        ".product-price",
        "span.PricesalesPrice",
        "div.PricesalesPrice",
        ".vm-price-value span.PricesalesPrice",
        ".PricesalesPrice",
        ".price-sales",
        "span.PricebasePriceWithTax",
        "div.PricebasePriceWithTax",
        "span.PricebasePriceVariant",
        "span.PricebasePrice",
        "span.PricepriceWithoutTax",
        "p.price ins .woocommerce-Price-amount",
        "p.price .woocommerce-Price-amount",
        ".summary .price .woocommerce-Price-amount",
        ".woocommerce-Price-amount",
        ".special-price .price",
        ".price-final_price .price",
        ".price-crossed",
        ".price",
    ]
    for sel in dom_selectors:
        for el in soup.select(sel):
            for candidate_val in (
                el.get("content"),
                el.get("data-price-amount"),
                el.get_text(" ", strip=True),
            ):
                if candidate_val:
                    price = _parse_price_string(candidate_val)
                    if price is not None:
                        return price

    # 5. Regex su contenitori scheda prodotto vicino a € / EUR
    euro_pattern = re.compile(
        r"(?:€|EUR)\s*([0-9]{1,4}(?:[.,][0-9]{3})*[.,][0-9]{1,2})|([0-9]{1,4}(?:[.,][0-9]{3})*[.,][0-9]{1,2})\s*(?:€|EUR)",
        re.IGNORECASE,
    )
    product_containers = soup.select(
        ".productdetails-view, .productdetails, #product-details, "
        ".product-main, .product-info-main, .product-prices, .summary, article.product, main"
    )
    for container in product_containers:
        text_block = container.get_text(" ", strip=True).replace("\xa0", " ")
        m = euro_pattern.search(text_block)
        if m:
            raw_num = m.group(1) or m.group(2)
            price = _parse_price_string(raw_num)
            if price is not None:
                return price

    # 6. Fallback finale AI con Gemini SOLO se la pagina è valida (> 500 caratteri e non è un blocco/captcha)
    if _get_gemini_api_key():
        for tag in soup(["script", "style", "noscript", "svg", "footer", "nav"]):
            tag.decompose()
        clean_text = " ".join(soup.get_text(separator=" ").split())[:8000]
        if not _is_blocked_or_captcha_html(html, clean_text):
            prompt = (
                f"Estrai il prezzo di vendita attuale (in Euro, IVA inclusa se presente) "
                f"dalla seguente pagina prodotto per l'articolo '{descrizione}'.\n"
                f"Rispondi ESCLUSIVAMENTE con il numero decimale usando il punto (es. 64.90). "
                f"Se nella pagina non è indicato alcun prezzo di vendita, rispondi con NONE.\n\n"
                f"Testo pagina:\n{clean_text}"
            )
            ai_res = _call_gemini_text(prompt)
            if ai_res and "NONE" not in ai_res.upper():
                price = _parse_price_string(ai_res)
                if price is not None:
                    return price

    return None


# =========================================================================
# WORKER PURO THREAD-SAFE + ESECUZIONE PARALLELA CON SALVATAGGIO IMMEDIATO
# =========================================================================


def _worker_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Worker eseguito nei thread secondari.
    Non chiama mai funzioni st.* né accede a st.session_state.
    """
    task_type = task["task_type"]
    p_id = task["prodotto_id"]
    sku = task.get("sku")
    desc = task.get("descrizione") or ""
    marchio = task.get("marchio") or ""
    dominio = (task.get("dominio") or "").strip()
    existing_url = (task.get("existing_url") or "").strip()
    discover_missing = bool(task.get("discover_missing_urls", True))

    url_finale = existing_url
    found_new_url = False

    # Salta la ricerca URL se il link è già salvato; cerca se mancante
    if not url_finale and dominio and discover_missing:
        found = trova_url_prodotto(dominio, sku, desc, marchio)
        if found:
            url_finale = found
            found_new_url = True

    scraped_price = None
    if url_finale:
        scraped_price = estrai_prezzo_da_url(url_finale, desc)

    # Se è un nostro prodotto con mio_prezzo == 0 e l'eventuale URL esistente non ha restituito prezzo,
    # in modalità discover_missing cerca comunque l'URL aggiornato sul nostro sito ed estrae il prezzo
    if (
        task_type == "mio"
        and (scraped_price is None or scraped_price <= 0)
        and float(task.get("old_price") or 0.0) <= 0.0
        and existing_url
        and dominio
        and discover_missing
    ):
        alt_found = trova_url_prodotto(dominio, sku, desc, marchio)
        if alt_found and alt_found != url_finale:
            alt_price = estrai_prezzo_da_url(alt_found, desc)
            if alt_price is not None and alt_price > 0:
                url_finale = alt_found
                found_new_url = True
                scraped_price = alt_price

    return {
        "task_type": task_type,
        "prodotto_id": p_id,
        "competitor_id": task.get("competitor_id"),
        "comp_nome": task.get("comp_nome"),
        "descrizione": desc,
        "dominio": dominio,
        "url_trovato": url_finale or None,
        "found_new_url": found_new_url,
        "prezzo": round(float(scraped_price), 2) if scraped_price and scraped_price > 0 else None,
        "old_price": task.get("old_price"),
        "old_prev": task.get("old_prev"),
    }


def esegui_auto_discovery_e_scraping(
    progress_callback: Optional[Callable[[float, str], None]] = None,
    discover_missing_urls: bool = True,
) -> Dict[str, Any]:
    """
    Esegue in parallelo (ThreadPoolExecutor max_workers=6) con:
    - Salvataggio IMMEDIATO step-by-step su Supabase appena ogni singolo task termina.
    - Controllo di tempo massimo per singolo step (max 8 secondi) e shutdown non bloccante
      così nessun sito lento può mai bloccare la barra di avanzamento.
    """
    load_dotenv(override=True)

    from db import (
        get_competitors_prezzi,
        get_impostazioni,
        get_prodotti,
        get_prodotti_competitor,
        trigger_global_refresh,
        update_prodotto,
        upsert_prodotti_competitor,
    )

    impostazioni = get_impostazioni()
    mio_sito = (impostazioni.get("sito_web") or "").strip() or "https://www.mondoirrigazione.it"
    prodotti = get_prodotti()
    competitors = get_competitors_prezzi(only_active=True)
    associazioni = get_prodotti_competitor()

    assoc_map: Dict[Tuple[str, str], Dict[str, Any]] = {
        (str(a["prodotto_id"]), str(a["competitor_id"])): a for a in associazioni
    }

    logs: List[str] = []
    stats: Dict[str, Any] = {
        "miei_url_trovati": 0,
        "miei_prezzi_aggiornati": 0,
        "comp_url_trovati": 0,
        "comp_prezzi_aggiornati": 0,
        "logs": logs,
    }

    tasks: List[Dict[str, Any]] = []
    for prod in prodotti:
        p_id = str(prod["id"])
        sku = prod.get("sku")
        desc = prod.get("descrizione") or ""
        marchio = prod.get("marchio") or ""
        mio_url = (prod.get("mio_url") or "").strip()
        mio_prezzo_attuale = round(float(prod.get("mio_prezzo") or 0.0), 2)

        if mio_url or (mio_sito and discover_missing_urls):
            tasks.append(
                {
                    "task_type": "mio",
                    "prodotto_id": p_id,
                    "competitor_id": None,
                    "comp_nome": "Nostro Sito",
                    "sku": sku,
                    "descrizione": desc,
                    "marchio": marchio,
                    "dominio": mio_sito,
                    "existing_url": mio_url,
                    "old_price": mio_prezzo_attuale,
                    "old_prev": None,
                    "discover_missing_urls": discover_missing_urls,
                }
            )

        for comp in competitors:
            c_id = str(comp["id"])
            c_nome = comp.get("nome") or "Competitor"
            c_dominio = (comp.get("dominio") or "").strip()
            existing = assoc_map.get((p_id, c_id), {})
            comp_url = (existing.get("url_prodotto") or "").strip()
            old_price = (
                round(float(existing["ultimo_prezzo"]), 2)
                if existing.get("ultimo_prezzo") is not None
                else None
            )
            old_prev = (
                round(float(existing["prezzo_precedente"]), 2)
                if existing.get("prezzo_precedente") is not None
                else None
            )

            if comp_url or (c_dominio and discover_missing_urls):
                tasks.append(
                    {
                        "task_type": "comp",
                        "prodotto_id": p_id,
                        "competitor_id": c_id,
                        "comp_nome": c_nome,
                        "sku": sku,
                        "descrizione": desc,
                        "marchio": marchio,
                        "dominio": c_dominio,
                        "existing_url": comp_url,
                        "old_price": old_price,
                        "old_prev": old_prev,
                        "discover_missing_urls": discover_missing_urls,
                    }
                )

    total_tasks = len(tasks)
    if total_tasks == 0:
        trigger_global_refresh()
        return stats

    completed_count = 0
    executor = ThreadPoolExecutor(max_workers=6)
    try:
        future_to_task = {executor.submit(_worker_task, t): t for t in tasks}
        pending = set(future_to_task.keys())

        while pending:
            # Attende al massimo 8 secondi per il prossimo future completato
            done, pending = wait(pending, timeout=8.0, return_when=FIRST_COMPLETED)

            if not done:
                # Se i task rimanenti sono bloccati oltre gli 8 secondi di inattività, li annulla
                for hung_f in list(pending):
                    t_info = future_to_task[hung_f]
                    hung_f.cancel()
                    completed_count += 1
                    lbl = t_info.get("comp_nome") or "Sito"
                    desc_s = t_info.get("descrizione") or ""
                    if progress_callback:
                        progress_callback(
                            min(completed_count / total_tasks, 1.0),
                            f"[{completed_count}/{total_tasks}] Timeout superato per {lbl}: {desc_s[:35]}...",
                        )
                    logs.append(
                        f":material/schedule: **[{lbl}]** {desc_s} -> Timeout (8s) superato su `{clean_domain(t_info.get('dominio', ''))}`"
                    )
                pending.clear()
                break

            for future in done:
                completed_count += 1
                t_info = future_to_task[future]
                label_target = t_info.get("comp_nome") or "Sito"
                desc_short = (t_info.get("descrizione") or "")[:38]

                if progress_callback:
                    progress_callback(
                        min(completed_count / total_tasks, 1.0),
                        f"[{completed_count}/{total_tasks}] {label_target}: {desc_short}...",
                    )

                try:
                    res = future.result(timeout=0.1)
                except Exception as exc:
                    logs.append(
                        f":material/error: **[{label_target}]** {t_info.get('descrizione')} -> Errore: {exc}"
                    )
                    continue

                p_id = res["prodotto_id"]
                desc = res["descrizione"]
                url_trovato = res["url_trovato"]
                found_new = res["found_new_url"]
                scraped_price = res["prezzo"]
                old_price = res["old_price"]
                old_prev = res["old_prev"]

                if res["task_type"] == "mio":
                    prod_updates: Dict[str, Any] = {}
                    existing_mio_url = (t_info.get("existing_url") or "").strip()
                    if url_trovato and (found_new or not existing_mio_url or url_trovato != existing_mio_url):
                        prod_updates["mio_url"] = url_trovato
                        if found_new or not existing_mio_url:
                            stats["miei_url_trovati"] += 1
                    if scraped_price is not None and scraped_price > 0:
                        stats["miei_prezzi_aggiornati"] += 1
                        if (
                            old_price is None
                            or float(old_price) <= 0.0
                            or round(float(scraped_price), 2) != round(float(old_price), 2)
                        ):
                            prod_updates["mio_prezzo"] = round(float(scraped_price), 2)

                    # Salvataggio IMMEDIATO step-by-step su Supabase
                    if prod_updates:
                        try:
                            update_prodotto(p_id, prod_updates)
                        except Exception as exc:
                            logs.append(
                                f":material/error: **[Nostro Sito]** {desc} -> Errore DB: {exc}"
                            )

                    if url_trovato:
                        p_txt = (
                            f"€ {scraped_price:.2f}"
                            if scraped_price is not None
                            else f"€ {old_price:.2f} (invariato)"
                        )
                        u_lbl = "Nuovo URL trovato" if found_new else "URL salvato"
                        logs.append(
                            f":material/check_circle: **[Nostro Sito]** {desc} -> [{u_lbl}]({url_trovato}) | Prezzo: **{p_txt}**"
                        )
                    else:
                        logs.append(
                            f":material/warning: **[Nostro Sito]** {desc} -> URL non trovato su `{clean_domain(res['dominio'])}`"
                        )

                else:
                    c_id = res["competitor_id"]
                    c_nome = res["comp_nome"]
                    if found_new and url_trovato:
                        stats["comp_url_trovati"] += 1
                    if scraped_price is not None:
                        stats["comp_prezzi_aggiornati"] += 1

                    final_price = scraped_price if scraped_price is not None else old_price

                    # Salvataggio IMMEDIATO step-by-step su Supabase per il singolo prodotto/competitor
                    if url_trovato or final_price is not None:
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
                                        "url_prodotto": url_trovato,
                                        "ultimo_prezzo": final_price,
                                        "prezzo_precedente": prev_to_save,
                                    }
                                ],
                                record_history=True,
                            )
                        except Exception as exc:
                            logs.append(
                                f":material/error: **[{c_nome}]** {desc} -> Errore salvataggio DB: {exc}"
                            )
                            continue

                        u_lbl = "Nuovo URL trovato" if found_new else "Da URL salvato"
                        if scraped_price is not None:
                            logs.append(
                                f":material/check_circle: **[{c_nome}]** {desc} -> [{u_lbl}]({url_trovato}) | Prezzo: **€ {scraped_price:.2f}**"
                            )
                        else:
                            logs.append(
                                f":material/info: **[{c_nome}]** {desc} -> [{u_lbl}]({url_trovato}) | Prezzo: *Non rilevato*"
                            )
                    else:
                        logs.append(
                            f":material/warning: **[{c_nome}]** {desc} -> Non trovato su `{clean_domain(res['dominio'])}`"
                        )
    finally:
        # Non blocca mai l'uscita se un thread di rete è rimasto appeso
        executor.shutdown(wait=False, cancel_futures=True)

    trigger_global_refresh()
    return stats


# =========================================================================
# MODULO SEO: ANALISI SERP (TOP 100) + STIMA VOLUME MENSILE (AI / EURISTICA)
# =========================================================================

_GOOGLE_ADS_VOLUME_BUCKETS = [
    10,
    20,
    30,
    40,
    50,
    70,
    90,
    110,
    140,
    170,
    210,
    260,
    320,
    390,
    480,
    590,
    720,
    880,
    1000,
    1300,
    1600,
    1900,
    2400,
    2900,
    3600,
    4400,
    5400,
    6600,
    8100,
    9900,
    12100,
    14800,
    18100,
    22200,
    27100,
    33100,
    40500,
    49500,
    60500,
    74000,
    90500,
    110000,
]


def _snap_to_google_ads_bucket(val: int) -> int:
    """Arrotonda un numero intero al valore standard Google Ads / Keyword Planner più vicino."""
    if val <= 10:
        return 10
    return min(_GOOGLE_ADS_VOLUME_BUCKETS, key=lambda b: abs(b - val))


def _heuristic_volume_fallback(keyword: str) -> int:
    """Formula euristica di fallback basata su lunghezza e genericità della query."""
    kw_clean = (keyword or "").strip().lower()
    if not kw_clean:
        return 10

    words = [w for w in re.split(r"\s+", kw_clean) if w]
    num_words = len(words)
    char_len = len(kw_clean)
    has_digits = bool(re.search(r"\d", kw_clean))

    if num_words == 1:
        base = 2400 if char_len <= 10 else 1300
    elif num_words == 2:
        base = 880 if not has_digits else 320
    elif num_words == 3:
        base = 390 if not has_digits else 170
    elif num_words == 4:
        base = 170 if not has_digits else 90
    else:
        base = 70 if not has_digits else 30

    # Variazione deterministica leggera basata sull'hash della keyword per evitare valori piatti
    jitter_factors = [0.75, 0.9, 1.0, 1.15, 1.3]
    factor = jitter_factors[sum(ord(c) for c in kw_clean) % len(jitter_factors)]
    return _snap_to_google_ads_bucket(int(round(base * factor)))


def stima_volume_ricerca(keyword: str) -> int:
    """
    Stima il volume di ricerca mensile medio in Italia (stile SEOZoom / Keyword Planner):
    - Chiede a gemini-2.5-flash (timeout=6s) di restituire SOLO un numero intero realistico
      arrotondato ai valori standard Google Ads (10, 20, 50, 90, 140, 210, 320, 480, 720, 1000, 1600, 2400, 3600, ecc.).
    - Se Gemini non è disponibile o non risponde in tempo, usa la formula euristica di fallback.
    """
    kw_clean = (keyword or "").strip()
    if not kw_clean:
        return 10

    if _get_gemini_api_key():
        prompt = (
            f"Agisci come un esperto SEO e Google Keyword Planner per il mercato italiano (Google.it).\n"
            f"Stima il volume di ricerca mensile medio in Italia per la keyword: \"{kw_clean}\" "
            f"(settore e-commerce irrigazione, giardinaggio, idraulica e bricolage).\n"
            f"Rispondi ESCLUSIVAMENTE con un singolo numero intero scelto tra i valori standard Google Ads "
            f"(10, 20, 30, 50, 70, 90, 110, 140, 170, 210, 260, 320, 390, 480, 590, 720, 880, "
            f"1000, 1300, 1600, 1900, 2400, 2900, 3600, 4400, 5400, 6600, 8100, 9900, 12100, 14800).\n"
            f"Non aggiungere testo, simboli o spiegazioni: restituisci solo il numero intero."
        )
        ai_res = _call_gemini_text(prompt)
        if ai_res:
            m = re.search(r"\b(\d{1,7})\b", ai_res.replace(".", "").replace(",", ""))
            if m:
                try:
                    raw_int = int(m.group(1))
                    if raw_int > 0:
                        return _snap_to_google_ads_bucket(raw_int)
                except ValueError:
                    pass

    return _heuristic_volume_fallback(kw_clean)


def _is_google_redirect_url(url: str) -> bool:
    """Verifica se un URL è un redirect interno Google Merchant/Shopping (/goto?url= o /url?q=)."""
    if not url:
        return False
    low = str(url).strip().lower()
    return bool(
        re.search(
            r"https?://(?:www\.)?google\.[a-z.]+/(?:goto|url|aclk|search)\?",
            low,
        )
    )


def unwrap_google_url(url: str) -> str:
    """
    Se l'URL contiene `google.com/goto?url=CAES...` (o `/url?q=`), estrae l'URL reale sottostante
    decodificando il payload base64 protobuf con `base64.urlsafe_b64decode` + regex URL.
    """
    if not url:
        return ""
    raw = str(url).strip()
    if not _is_google_redirect_url(raw) and "url=CAES" not in raw:
        return raw

    try:
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        for param_key in ("url", "q", "adurl"):
            vals = qs.get(param_key)
            if not vals:
                continue
            val = vals[0].strip()
            if val.startswith(("http://", "https://")):
                return unquote(val)

            # Decodifica token base64 protobuf (es. CAES...)
            padded = val + "=" * (-len(val) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded)
            except Exception:
                continue

            found_bytes = re.findall(
                rb'https?://[^\x00-\x1f\x7f-\xff"\'\s<>]+',
                decoded,
            )
            candidates: List[str] = []
            for fb in found_bytes:
                try:
                    u_str = fb.decode("utf-8", errors="ignore").strip()
                    # Rimuove eventuali caratteri di controllo protobuf finali attaccati all'URL
                    u_str = re.sub(r"[\x00-\x20\x7f-\xff]+.*$", "", u_str)
                    u_str = u_str.rstrip(").,;:\\")
                    if u_str.startswith(("http://", "https://")):
                        candidates.append(u_str)
                except Exception:
                    continue

            non_google = [
                c for c in candidates if "google." not in clean_domain(c)
            ]
            if non_google:
                return non_google[0]
            if candidates:
                return candidates[0]
    except Exception:
        pass

    return raw


def _domain_matches(url_or_domain: str, target_clean_dom: str) -> bool:
    """Verifica se l'URL appartiene al dominio target (inclusi eventuali sottodomini)."""
    if not url_or_domain or not target_clean_dom:
        return False
    link_dom = clean_domain(unwrap_google_url(url_or_domain))
    if not link_dom:
        return False
    return link_dom == target_clean_dom or link_dom.endswith("." + target_clean_dom)


def analizza_serp_keyword(
    keyword: str,
    mio_dominio: str,
    lista_competitors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Interroga Google.it tramite Serper.dev (num=100, timeout=8) per `keyword`.
    Considera ESCLUSIVAMENTE la chiave `organic` (ignorando `ads`, `shopping`, `inlineShopping`, `places`),
    dando priorità assoluta ai risultati organici SEO puri (escludendo redirect `google.com/goto`
    dalla Top 5 quando ci sono risultati organici standard e decodificandoli sempre con `unwrap_google_url`).
    """
    clean_mio = clean_domain(mio_dominio)
    comp_targets: List[Tuple[str, str]] = []
    for comp in lista_competitors:
        c_id = str(comp.get("id") or "")
        c_dom = clean_domain(comp.get("dominio") or "")
        if c_id and c_dom:
            comp_targets.append((c_id, c_dom))

    result: Dict[str, Any] = {
        "mio": {"posizione": None, "url_posizionato": None},
        "competitors": {
            c_id: {"posizione": None, "url_posizionato": None}
            for c_id, _ in comp_targets
        },
        "top_5_serp": [],
    }

    serper_key = _get_serper_api_key()
    kw_clean = (keyword or "").strip()
    if not serper_key or not kw_clean:
        return result

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": serper_key,
                "Content-Type": "application/json",
            },
            json={
                "q": kw_clean,
                "gl": "it",
                "hl": "it",
                "num": 100,
            },
            timeout=(3, 8),
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return result
        data = resp.json()
        raw_organic = data.get("organic") or []
    except Exception:
        return result

    # Separa i risultati organici SEO puri dai caroselli Merchant/Shopping (google.com/goto)
    pure_organic_items: List[Dict[str, Any]] = []
    unwrapped_goto_items: List[Dict[str, Any]] = []

    for item in raw_organic:
        raw_link = (item.get("link") or "").strip()
        if not raw_link:
            continue
        is_goto = _is_google_redirect_url(raw_link) or "url=CAES" in raw_link
        real_link = unwrap_google_url(raw_link)
        real_dom = clean_domain(real_link)
        # Scarta qualsiasi link che resti ancora su dominio google.*
        if not real_dom or real_dom.startswith("google."):
            continue

        entry = {
            "title": (item.get("title") or "").strip() or real_link,
            "link": real_link,
            "domain": real_dom,
        }
        if not is_goto:
            pure_organic_items.append(entry)
        else:
            unwrapped_goto_items.append(entry)

    # Priorità assoluta ai risultati organici SEO puri (usa i goto decodificati solo come coda di completamento)
    ordered_organic = (pure_organic_items + unwrapped_goto_items)[:100]

    # Top 5 Attuale in SERP: usa i risultati organici puri (e completa fino a 5 se necessario)
    top_5_source = (
        pure_organic_items[:5]
        if len(pure_organic_items) >= 5
        else ordered_organic[:5]
    )
    result["top_5_serp"] = [
        {
            "pos": idx_top,
            "title": item_top["title"],
            "link": item_top["link"],
            "domain": item_top["domain"],
        }
        for idx_top, item_top in enumerate(top_5_source, start=1)
    ]

    # Calcolo posizioni organiche 1..100
    for idx, item in enumerate(ordered_organic, start=1):
        link = item["link"]
        pos = idx

        # Check nostro dominio (prima posizione utile)
        if (
            clean_mio
            and result["mio"]["posizione"] is None
            and _domain_matches(link, clean_mio)
        ):
            result["mio"] = {"posizione": pos, "url_posizionato": link}

        # Check ciascun competitor SEO (prima posizione utile)
        for c_id, c_dom in comp_targets:
            if (
                result["competitors"][c_id]["posizione"] is None
                and _domain_matches(link, c_dom)
            ):
                result["competitors"][c_id] = {
                    "posizione": pos,
                    "url_posizionato": link,
                }

    return result


def _seo_worker_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Worker puro (thread-safe, zero chiamate st.*) per analizzare SERP + stimare volume
    di una singola keyword.
    """
    kw_row = task["keyword_row"]
    kw_text = (kw_row.get("keyword") or "").strip()
    mio_dominio = task.get("mio_dominio") or ""
    competitors = task.get("competitors") or []

    serp_data = analizza_serp_keyword(kw_text, mio_dominio, competitors)

    existing_vol = kw_row.get("volume_mensile")
    vol_int: Optional[int] = None
    if existing_vol is not None:
        try:
            v = int(float(existing_vol))
            if v > 0:
                vol_int = v
        except (ValueError, TypeError):
            vol_int = None

    if vol_int is None:
        vol_int = stima_volume_ricerca(kw_text)

    comp_list_results: List[Dict[str, Any]] = []
    for comp in competitors:
        c_id = str(comp["id"])
        c_serp = serp_data["competitors"].get(
            c_id, {"posizione": None, "url_posizionato": None}
        )
        comp_list_results.append(
            {
                "competitor_id": c_id,
                "posizione": c_serp.get("posizione"),
                "url_posizionato": c_serp.get("url_posizionato"),
            }
        )

    return {
        "keyword_row": kw_row,
        "mia_posizione": serp_data["mio"].get("posizione"),
        "mio_url": serp_data["mio"].get("url_posizionato"),
        "volume_mensile": vol_int,
        "competitors_results": comp_list_results,
        "top_5_serp": serp_data.get("top_5_serp") or [],
    }


def esegui_aggiornamento_seo(
    progress_callback: Optional[Callable[[float, str], None]] = None,
    only_missing_position: bool = False,
    target_keyword_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Orchestratore parallelo (ThreadPoolExecutor max_workers=5) per il Modulo SEO:
    - Analizza la SERP top 100 e stima i volumi mensili mancanti per le keyword selezionate.
    - Salva immediatamente step-by-step su Supabase ogni keyword completata.
    """
    load_dotenv(override=True)

    from db import (
        get_competitors_seo,
        get_impostazioni,
        get_seo_keywords,
        salva_rilevazione_serp_keyword,
        trigger_global_refresh,
    )

    impostazioni = get_impostazioni()
    mio_dominio = (impostazioni.get("sito_web") or "").strip()
    all_keywords = get_seo_keywords()
    competitors_seo = get_competitors_seo(only_active=True)

    if target_keyword_id is not None:
        keywords = [
            kw for kw in all_keywords if str(kw.get("id")) == str(target_keyword_id)
        ]
    elif only_missing_position:
        keywords = [
            kw
            for kw in all_keywords
            if kw.get("mia_posizione_attuale") is None and not kw.get("ultimo_check")
        ]
        # Se tutte hanno già ultimo_check ma qualcuna non ha posizione o volume, includiamole
        if not keywords:
            keywords = [
                kw
                for kw in all_keywords
                if kw.get("mia_posizione_attuale") is None
                or not kw.get("volume_mensile")
            ]
    else:
        keywords = all_keywords

    logs: List[str] = []
    stats: Dict[str, Any] = {
        "keywords_analizzate": 0,
        "posizionati_top100": 0,
        "logs": logs,
    }

    total_kw = len(keywords)
    if total_kw == 0:
        trigger_global_refresh()
        return stats

    tasks = [
        {
            "keyword_row": kw,
            "mio_dominio": mio_dominio,
            "competitors": competitors_seo,
        }
        for kw in keywords
    ]

    completed_count = 0
    executor = ThreadPoolExecutor(max_workers=5)
    try:
        future_to_task = {executor.submit(_seo_worker_task, t): t for t in tasks}
        pending = set(future_to_task.keys())

        while pending:
            done, pending = wait(pending, timeout=12.0, return_when=FIRST_COMPLETED)

            if not done:
                for hung_f in list(pending):
                    t_info = future_to_task[hung_f]
                    hung_f.cancel()
                    completed_count += 1
                    kw_name = (t_info["keyword_row"].get("keyword") or "")[:40]
                    if progress_callback:
                        progress_callback(
                            min(completed_count / total_kw, 1.0),
                            f"[{completed_count}/{total_kw}] Timeout SERP su '{kw_name}'...",
                        )
                    logs.append(
                        f":material/schedule: **{kw_name}** -> Timeout superato durante l'analisi SERP"
                    )
                pending.clear()
                break

            for future in done:
                completed_count += 1
                t_info = future_to_task[future]
                kw_name = (t_info["keyword_row"].get("keyword") or "")

                if progress_callback:
                    progress_callback(
                        min(completed_count / total_kw, 1.0),
                        f"[{completed_count}/{total_kw}] SERP: {kw_name[:40]}...",
                    )

                try:
                    res = future.result(timeout=0.1)
                except Exception as exc:
                    logs.append(
                        f":material/error: **{kw_name}** -> Errore analisi SERP: {exc}"
                    )
                    continue

                mia_pos = res["mia_posizione"]
                mio_url = res["mio_url"]
                vol_mensile = res["volume_mensile"]
                comp_results = res["competitors_results"]
                top_5_serp = res.get("top_5_serp") or []

                try:
                    salva_rilevazione_serp_keyword(
                        keyword_row=res["keyword_row"],
                        nuova_mia_posizione=mia_pos,
                        mio_url_posizionato=mio_url,
                        risultati_competitors=comp_results,
                        volume_stimato=vol_mensile,
                        top_5_serp=top_5_serp,
                    )
                    stats["keywords_analizzate"] += 1
                    if mia_pos is not None:
                        stats["posizionati_top100"] += 1
                except Exception as exc:
                    logs.append(
                        f":material/error: **{kw_name}** -> Errore salvataggio DB: {exc}"
                    )
                    continue

                pos_str = f"#{mia_pos}" if mia_pos is not None else "> 100"
                vol_str = f"{vol_mensile:,}".replace(",", ".") if vol_mensile else "—"
                logs.append(
                    f":material/check_circle: **{kw_name}** -> Posizione: **{pos_str}** | Volume: **{vol_str}/mese**"
                )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    trigger_global_refresh()
    return stats


# =========================================================================
# MODULO 3: MOTORE DI VERIFICA BACKLINK (PARALLELO + ANTI-BOT)
# =========================================================================


def _fetch_html_with_status(url: str, timeout: int = 10) -> Tuple[int, Optional[str]]:
    """
    Scarica `url` usando `curl_cffi` con `impersonate="chrome120"`, `timeout=10`,
    `allow_redirects=True` (con fallback su `requests` standard e User-Agent desktop)
    e restituisce `(http_status, html_text)`.
    """
    if not url:
        return 0, None

    target = str(url).strip()
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    req_headers = {
        **HEADERS,
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
    }

    last_status = 0

    if cffi_requests is not None:
        for verify_ssl in (True, False):
            try:
                resp = cffi_requests.get(
                    target,
                    impersonate="chrome120",
                    headers=req_headers,
                    timeout=timeout,
                    allow_redirects=True,
                    verify=verify_ssl,
                )
                status = int(resp.status_code or 0)
                if status > 0:
                    last_status = status
                if status == 200 and resp.text:
                    return 200, resp.text
                if status > 0 and verify_ssl:
                    # Se il server ha risposto con un codice HTTP != 200, proviamo il fallback requests
                    break
            except Exception:
                continue

    for verify_ssl in (True, False):
        try:
            resp_std = requests.get(
                target,
                headers=req_headers,
                timeout=(4, timeout),
                allow_redirects=True,
                verify=verify_ssl,
            )
            status = int(resp_std.status_code or 0)
            if status > 0:
                last_status = status
            if status == 200 and resp_std.text:
                return 200, resp_std.text
            if status > 0 and verify_ssl:
                break
        except Exception:
            continue

    return last_status, None


def _normalize_url_for_match(url: str) -> str:
    """Normalizza un URL rimuovendo protocollo, www., parametri finali vuoti e slash finale."""
    if not url:
        return ""
    raw = str(url).strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}".lower()


def _extract_rel_type(a_tag: Any) -> str:
    """
    Legge l'attributo `rel` del tag `<a>`:
    - Se non ha `rel` (o non contiene `nofollow`, `sponsored` o `ugc`), restituisce `"dofollow"`.
    - Altrimenti restituisce il valore specifico rilevato.
    """
    raw_rel = a_tag.get("rel")
    if not raw_rel:
        return "dofollow"

    if isinstance(raw_rel, (list, tuple)):
        rel_tokens = [str(t).strip().lower() for t in raw_rel if str(t).strip()]
    else:
        rel_tokens = [
            t.strip().lower()
            for t in re.split(r"[\s,]+", str(raw_rel))
            if t.strip()
        ]

    if not rel_tokens:
        return "dofollow"

    for special in ("sponsored", "ugc", "nofollow"):
        if special in rel_tokens:
            return special

    return "dofollow"


def _extract_anchor_text(a_tag: Any) -> str:
    """
    Estrae il testo cliccabile dentro il tag `<a>` (`a.get_text(strip=True)`),
    oppure `[Immagine: alt]` se contiene un tag `<img>` senza testo.
    """
    txt = " ".join(a_tag.get_text(" ", strip=True).split())
    if txt:
        return txt[:250]

    img = a_tag.find("img")
    if img is not None:
        alt_txt = " ".join(str(img.get("alt") or img.get("title") or "").split())
        if alt_txt:
            return f"[Immagine: {alt_txt[:200]}]"
        return "[Immagine: senza alt]"

    title_attr = " ".join(str(a_tag.get("title") or a_tag.get("aria-label") or "").split())
    if title_attr:
        return title_attr[:250]

    return "[Link senza testo]"


def _build_brand_variants(
    nome_azienda: Optional[str],
    mio_dominio: Optional[str],
    target_dom: Optional[str],
) -> List[str]:
    """
    Costruisce dinamicamente le varianti del brand (case-insensitive) a partire da
    `nome_azienda` e `mio_dominio` / `target_dom`, includendo sempre:
    'mondoirrigazione.it', 'mondoirrigazione', 'mondo irrigazione', 'mondo-irrigazione'.
    """
    variants: List[str] = [
        "mondoirrigazione.it",
        "mondoirrigazione",
        "mondo irrigazione",
        "mondo-irrigazione",
    ]

    for dom_candidate in (target_dom, clean_domain(mio_dominio or "")):
        if not dom_candidate:
            continue
        dc = dom_candidate.strip().lower()
        if dc and dc not in variants:
            variants.append(dc)
        root_name = dc.split(".")[0].strip()
        if len(root_name) >= 4:
            if root_name not in variants:
                variants.append(root_name)
            spaced = root_name.replace("-", " ").strip()
            if spaced and spaced not in variants:
                variants.append(spaced)

    if nome_azienda:
        cleaned_brand = re.sub(
            r"\b(seo\s*suite|dashboard|suite|s\.?r\.?l\.?|s\.?p\.?a\.?|s\.?n\.?c\.?)\b",
            "",
            nome_azienda,
            flags=re.IGNORECASE,
        ).strip(" -_.,|")
        if len(cleaned_brand) >= 4:
            b_low = " ".join(cleaned_brand.lower().split())
            b_nospace = re.sub(r"[\s\-_]+", "", b_low)
            b_hyphen = re.sub(r"[\s_]+", "-", b_low)
            for cand in (b_low, b_nospace, b_hyphen):
                if len(cand) >= 4 and cand not in variants:
                    variants.append(cand)

    return variants


def _extract_brand_mention_snippet(visible_text: str, variants: List[str]) -> Optional[str]:
    """
    Cerca nel testo visibile della pagina una qualsiasi delle varianti del brand (case-insensitive).
    Se trovata, restituisce `[Menzione: "...testo circostante..."]` (max 90 caratteri).
    """
    if not visible_text:
        return None

    for var in variants:
        if not var:
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(var) + r"(?!\w)", re.IGNORECASE)
        m = pattern.search(visible_text)
        if not m:
            m = re.search(re.escape(var), visible_text, re.IGNORECASE)
        if m:
            start_idx, end_idx = m.start(), m.end()
            ctx_start = max(0, start_idx - 28)
            ctx_end = min(len(visible_text), end_idx + 28)
            raw_snippet = visible_text[ctx_start:ctx_end].strip()
            raw_snippet = " ".join(raw_snippet.split()).replace('"', "'")
            prefix = "..." if ctx_start > 0 else ""
            suffix = "..." if ctx_end < len(visible_text) else ""
            core = f"{prefix}{raw_snippet}{suffix}"
            # Garantisce che l'intera stringa '[Menzione: "..."]' non superi 90 caratteri
            max_core_len = 90 - len('[Menzione: ""]')
            if len(core) > max_core_len:
                core = core[: max_core_len - 3].rstrip() + "..."
            return f'[Menzione: "{core}"]'

    return None


def verifica_singolo_backlink(
    url_sorgente: str,
    url_destinazione_atteso: Optional[str],
    mio_dominio: str,
    nome_azienda: str = "",
) -> Dict[str, Any]:
    """
    1. Determina `target_dom = clean_domain(url_destinazione_atteso) or clean_domain(mio_dominio) or 'mondoirrigazione.it'`.
    2. Scarica `url_sorgente` con `curl_cffi` (`impersonate="chrome120"`, `timeout=10`, `allow_redirects=True`)
       e fallback `requests.get`.
    3. Caso A (Link `<a>` presente): cerca i tag `<a href="...">` con `BeautifulSoup` (o regex HTML per URL verso `target_dom`).
    4. Caso B (Nessun link `<a>`, ma Menzione Testuale presente): cerca nel testo visibile della pagina
       le varianti del brand (`Mondoirrigazione`, `Mondo Irrigazione`, `mondoirrigazione.it`, `nome_azienda`, ecc.).
       Se trovata, imposta `stato_attivo = True`, `rel_attribute = "menzione"`, e `anchor_text = '[Menzione: "..."]'`.
    """
    src_clean = (url_sorgente or "").strip()
    expected_clean = (url_destinazione_atteso or "").strip()
    target_dom = (
        clean_domain(expected_clean)
        or clean_domain(mio_dominio)
        or "mondoirrigazione.it"
    )

    http_status, html = _fetch_html_with_status(src_clean, timeout=10)

    result: Dict[str, Any] = {
        "stato_attivo": False,
        "http_status": http_status,
        "url_destinazione_trovato": None,
        "anchor_text": None,
        "rel_attribute": None,
        "target_dom": target_dom,
    }

    if http_status != 200 or not html:
        return result

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    expected_norm = _normalize_url_for_match(expected_clean) if expected_clean else ""
    target_root_norm = _normalize_url_for_match(target_dom) if target_dom else ""
    has_specific_expected_path = bool(
        expected_norm and target_root_norm and expected_norm != target_root_norm
    )

    exact_match_tag = None
    exact_match_href = None
    domain_match_tag = None
    domain_match_href = None

    for a_tag in soup.find_all("a"):
        raw_href = (
            a_tag.get("href")
            or a_tag.get("data-href")
            or a_tag.get("data-url")
            or ""
        ).strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        full_href = unwrap_google_url(urljoin(src_clean, raw_href))
        link_dom = clean_domain(full_href)
        href_norm = _normalize_url_for_match(full_href)

        if has_specific_expected_path and href_norm == expected_norm:
            exact_match_tag = a_tag
            exact_match_href = full_href
            break

        if target_dom and (
            link_dom == target_dom
            or target_dom in link_dom
            or target_dom in full_href.lower()
        ):
            if domain_match_tag is None:
                domain_match_tag = a_tag
                domain_match_href = full_href
            if not has_specific_expected_path:
                break

    chosen_tag = exact_match_tag or domain_match_tag
    chosen_href = exact_match_href or domain_match_href

    if chosen_tag is not None and chosen_href:
        result["stato_attivo"] = True
        result["url_destinazione_trovato"] = chosen_href
        result["anchor_text"] = _extract_anchor_text(chosen_tag)
        result["rel_attribute"] = _extract_rel_type(chosen_tag)
        return result

    # Fallback Regex sull'HTML grezzo per veri URL (http/https) che puntano a target_dom (es. link iniettati via JS/builder)
    if target_dom:
        url_regex = re.compile(
            r'https?:\\?/\?/[a-zA-Z0-9._-]*'
            + re.escape(target_dom)
            + r'[^\s"\'<>\\]*',
            re.IGNORECASE,
        )
        m_raw = url_regex.search(html)
        if m_raw:
            found_raw_url = m_raw.group(0).replace("\\/", "/")
            result["stato_attivo"] = True
            result["url_destinazione_trovato"] = found_raw_url
            result["anchor_text"] = "[Rilevato nel codice pagina]"
            result["rel_attribute"] = "dofollow"
            return result

    # Caso B: Nessun link <a> verso il nostro sito -> cerca Menzione Testuale del brand nel testo visibile
    for non_vis in soup(["script", "style", "noscript", "svg", "template"]):
        non_vis.decompose()
    visible_text = " ".join(soup.get_text(" ", strip=True).split())

    brand_variants = _build_brand_variants(nome_azienda, mio_dominio, target_dom)
    mention_snippet = _extract_brand_mention_snippet(visible_text, brand_variants)
    if mention_snippet:
        result["stato_attivo"] = True
        result["url_destinazione_trovato"] = None
        result["anchor_text"] = mention_snippet
        result["rel_attribute"] = "menzione"
        return result

    return result


def _backlink_worker_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Worker puro (thread-safe, zero chiamate st.*) per verificare un singolo backlink."""
    bl_row = task["backlink_row"]
    mio_dominio = task.get("mio_dominio") or ""
    nome_azienda = task.get("nome_azienda") or ""
    url_sorgente = (bl_row.get("url_sorgente") or "").strip()
    url_dest_atteso = (bl_row.get("url_destinazione") or "").strip()

    check_res = verifica_singolo_backlink(
        url_sorgente, url_dest_atteso, mio_dominio, nome_azienda=nome_azienda
    )
    return {
        "backlink_row": bl_row,
        **check_res,
    }


def esegui_verifica_backlinks(
    progress_callback: Optional[Callable[[float, str], None]] = None,
    target_backlink_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Orchestratore parallelo (ThreadPoolExecutor max_workers=6) per il Modulo Backlink:
    verifica tutti i backlink (o uno specifico) e salva progressivamente l'esito su Supabase.
    """
    load_dotenv(override=True)

    from db import (
        get_backlinks,
        get_impostazioni,
        salva_esito_backlink,
        trigger_global_refresh,
    )

    impostazioni = get_impostazioni()
    mio_dominio = (impostazioni.get("sito_web") or "").strip()
    nome_azienda = (impostazioni.get("nome_azienda") or "").strip()
    all_backlinks = get_backlinks()

    if target_backlink_id is not None:
        backlinks = [
            b for b in all_backlinks if str(b.get("id")) == str(target_backlink_id)
        ]
    else:
        backlinks = all_backlinks

    logs: List[str] = []
    stats: Dict[str, Any] = {
        "totale_verificati": 0,
        "attivi": 0,
        "menzioni": 0,
        "non_attivi": 0,
        "logs": logs,
    }

    total_bl = len(backlinks)
    if total_bl == 0:
        trigger_global_refresh()
        return stats

    tasks = [
        {
            "backlink_row": b,
            "mio_dominio": mio_dominio,
            "nome_azienda": nome_azienda,
        }
        for b in backlinks
    ]

    completed_count = 0
    executor = ThreadPoolExecutor(max_workers=6)
    try:
        future_to_task = {executor.submit(_backlink_worker_task, t): t for t in tasks}
        pending = set(future_to_task.keys())

        while pending:
            done, pending = wait(pending, timeout=10.0, return_when=FIRST_COMPLETED)

            if not done:
                for hung_f in list(pending):
                    t_info = future_to_task[hung_f]
                    hung_f.cancel()
                    completed_count += 1
                    bl_row = t_info["backlink_row"]
                    src_dom = clean_domain(bl_row.get("url_sorgente") or "")
                    if progress_callback:
                        progress_callback(
                            min(completed_count / total_bl, 1.0),
                            f"[{completed_count}/{total_bl}] Timeout su {src_dom}...",
                        )
                    try:
                        salva_esito_backlink(
                            backlink_id=str(bl_row["id"]),
                            stato_attivo=False,
                            http_status=0,
                            rel_attribute=bl_row.get("rel_attribute"),
                            anchor_text=bl_row.get("anchor_text"),
                        )
                        stats["totale_verificati"] += 1
                        stats["non_attivi"] += 1
                    except Exception:
                        pass
                    logs.append(
                        f":material/schedule: **{src_dom}** -> Timeout connessione (HTTP 0)"
                    )
                pending.clear()
                break

            for future in done:
                completed_count += 1
                t_info = future_to_task[future]
                bl_row = t_info["backlink_row"]
                src_url = (bl_row.get("url_sorgente") or "").strip()
                src_dom = clean_domain(src_url) or src_url[:35]

                if progress_callback:
                    progress_callback(
                        min(completed_count / total_bl, 1.0),
                        f"[{completed_count}/{total_bl}] Verifica: {src_dom}...",
                    )

                try:
                    res = future.result(timeout=0.1)
                except Exception as exc:
                    logs.append(
                        f":material/error: **{src_dom}** -> Errore verifica: {exc}"
                    )
                    continue

                is_active = bool(res.get("stato_attivo"))
                http_code = int(res.get("http_status") or 0)
                rel_attr = res.get("rel_attribute")
                anchor_txt = res.get("anchor_text")
                dest_found = res.get("url_destinazione_trovato")

                try:
                    salva_esito_backlink(
                        backlink_id=str(bl_row["id"]),
                        stato_attivo=is_active,
                        http_status=http_code,
                        rel_attribute=rel_attr,
                        anchor_text=anchor_txt,
                        url_destinazione_trovato=dest_found,
                    )
                    stats["totale_verificati"] += 1
                    if is_active:
                        stats["attivi"] += 1
                        if str(rel_attr or "").strip().lower() == "menzione":
                            stats["menzioni"] += 1
                    else:
                        stats["non_attivi"] += 1
                except Exception as exc:
                    logs.append(
                        f":material/error: **{src_dom}** -> Errore salvataggio DB: {exc}"
                    )
                    continue

                if is_active:
                    if str(rel_attr or "").strip().lower() == "menzione":
                        logs.append(
                            f":material/record_voice_over: **{src_dom}** -> **Menzione (No Link)** (HTTP {http_code}) | {anchor_txt}"
                        )
                    else:
                        logs.append(
                            f":material/check_circle: **{src_dom}** -> **Link Attivo** (HTTP {http_code} • `{rel_attr}`) | Anchor: *\"{anchor_txt}\"*"
                        )
                else:
                    logs.append(
                        f":material/cancel: **{src_dom}** -> **Non attivo** (HTTP {http_code})"
                    )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    trigger_global_refresh()
    return stats


