"""Multi-provider barcode metadata lookup and enrichment for Rip Manager."""
from __future__ import annotations
import re
from typing import Optional
import httpx
import db
import nodes as node_client

BARCODE=re.compile(r"^\d{6,14}$")
YEAR=re.compile(r"\b(19|20)\d{2}\b")
TV_HINT=re.compile(r"\bseasons?\s*\d|\bepisodes?\b|\btv series\b|\bcomplete series\b|\bcomplete collection\b|\bbox ?set\b",re.I)
SEASON=re.compile(r"\bseasons?\s*(\d{1,2})\b",re.I)
NOISE=re.compile(r"\b(?:dvd|blu[- ]?ray|blu[- ]?ray disc|4k(?:\s*uhd)?|uhd|ultra\s*(?:high\s*definition|hd)|2160p|1080p|720p|hdr10\+?|dolby vision|region\s*[a-c0-9]+|widescreen|full\s*screen|special\s*edition|collector'?s\s*edition|anniversary\s*edition|limited\s*edition|steelbook|digital\s*copy|new|sealed|the\s*complete\s*(?:collection|series|seasons?)|complete\s*(?:collection|series)|box\s*set|\d+\s*[- ]?\s*disc\s*(?:set|collection)?|\d+\s*[- ]?\s*dvd\s*(?:set|collection)?)\b",re.I)
CATALOGUE_TAIL=re.compile(r"\s*[,;|/\-–—]*\s*\b\d{8,14}\b(?:\s*[,;|/\-–—].*)?$",re.I)
PERSON_SEGMENT=re.compile(r"^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,3}$")

def normalise(code:str)->Optional[str]:
    code=re.sub(r"\D","",code or "")
    return code if BARCODE.match(code) else None

def clean_title(raw:str)->str:
    title=str(raw or "").strip()

    # Retail/catalogue databases sometimes append the barcode and cast directly
    # to the product title, e.g.
    # "The Imitation Game, 5055201827401, Benedict Cumberbatch, Keira Knightley".
    # Once a barcode-like catalogue number begins, everything after it is
    # product metadata rather than the media title.
    title=CATALOGUE_TAIL.sub("",title)

    # Also remove an obvious comma-separated cast tail when no barcode was
    # included. Require at least two consecutive person-like segments so normal
    # comma-containing titles such as "Planes, Trains and Automobiles" survive.
    parts=[part.strip(" .;") for part in title.split(",")]
    if len(parts)>=3 and PERSON_SEGMENT.match(parts[-1] or "") and PERSON_SEGMENT.match(parts[-2] or ""):
        parts=parts[:-2]
        title=", ".join(part for part in parts if part)

    title=NOISE.sub(" ",title)
    title=SEASON.sub(" ",title)
    title=re.sub(r"\[[^\]]*\]|\([^)]*\)"," ",title)
    title=re.sub(r"\s+([,.;:!?])",r"\1",title)
    title=re.sub(r"([,.;:])(?:\s*[,.;:])+",r"\1",title)
    title=re.sub(r"\s{2,}"," ",title)
    return title.strip(" -–—:;,./|")

def _year(text):
    m=YEAR.search(text or "");return int(m.group(0)) if m else None

def _item(source,title,media_type,year=None,season=None,creator=None,narrator=None,confidence=80,raw_title=None):
    return {"source":source,"title":clean_title(title) or title,"raw_title":raw_title or title,"year":year,
            "media_type":media_type,"season":season,"disc":1 if media_type=="tv" else None,
            "creator":creator,"narrator":narrator,"confidence":confidence}

async def _upc(code):
    if not db.get_setting_bool("metadata_upcitemdb",True):return []
    key=db.get_setting("metadata_upcitemdb_key","")
    paid=db.get_setting("metadata_upcitemdb_mode","free")=="paid" and bool(key)
    url="https://api.upcitemdb.com/prod/v1/lookup" if paid else "https://api.upcitemdb.com/prod/trial/lookup"
    headers={"Accept":"application/json"}
    if paid:headers.update({"user_key":key,"key_type":"3scale"})
    r=await node_client.client().get(url,params={"upc":code},headers=headers,timeout=8);r.raise_for_status()
    out=[]
    for item in (r.json().get("items") or [])[:8]:
        raw=str(item.get("title") or item.get("description") or "").strip()
        hay=" ".join(str(item.get(k) or "") for k in ("title","description","category","brand"))
        is_tv=bool(TV_HINT.search(hay) or SEASON.search(hay))
        # TV only when packaging explicitly indicates a series/season/box set.
        kind="tv" if is_tv else ("audiobook" if re.search(r"\baudio ?book|unabridged|abridged\b",hay,re.I) else ("music" if re.search(r"\baudio cd|music cd|album|soundtrack\b",hay,re.I) else "movie"))
        sm=SEASON.search(hay)
        out.append(_item("UPCitemdb",raw,kind,_year(hay),int(sm.group(1)) if sm else (1 if is_tv else None),
                         item.get("brand") if kind=="music" else None,confidence=86 if is_tv else 82,raw_title=raw))
    return out

async def _musicbrainz(code):
    if not db.get_setting_bool("metadata_musicbrainz",True):return []
    r=await node_client.client().get("https://musicbrainz.org/ws/2/release/",params={"query":f'barcode:"{code}"',"fmt":"json","limit":8},
        headers={"Accept":"application/json","User-Agent":"RipManager/0.11.0"},timeout=8);r.raise_for_status()
    out=[]
    for rel in r.json().get("releases",[]) or []:
        artist="".join((x.get("name") or (x.get("artist") or {}).get("name") or "") for x in rel.get("artist-credit",[]) if isinstance(x,dict))
        out.append(_item("MusicBrainz",rel.get("title") or "","music",_year(rel.get("date") or ""),creator=artist,confidence=96))
    return out

async def _books(code):
    if not db.get_setting_bool("metadata_google_books",True):return []
    params={"q":f"isbn:{code}","maxResults":8}
    key=db.get_setting("metadata_google_books_key","")
    if key:params["key"]=key
    r=await node_client.client().get("https://www.googleapis.com/books/v1/volumes",params=params,headers={"Accept":"application/json"},timeout=8);r.raise_for_status()
    out=[]
    for item in r.json().get("items",[]) or []:
        v=item.get("volumeInfo") or {}; title=v.get("title") or ""
        if v.get("subtitle"):title += ": "+v["subtitle"]
        out.append(_item("Google Books",title,"audiobook",_year(v.get("publishedDate") or ""),creator=", ".join(v.get("authors") or []),confidence=92))
    return out

def _title_key(value:str)->str:
    return re.sub(r"[^a-z0-9]+","",str(value or "").lower())


async def _omdb_enrich(item:dict)->dict:
    """Fill movie/TV title + year from OMDb using the cleaned UPC title.

    Direct title lookup (`t`) is preferred because OMDb returns one structured
    record including Year. Search (`s`) is only a fallback. Enrichment failure
    never discards the UPC match.
    """
    if item.get("media_type") not in {"movie","tv"}:
        return item
    if not db.get_setting_bool("metadata_omdb",True):
        return item
    key=db.get_setting("metadata_omdb_key","").strip()
    if not key:
        return item

    title=clean_title(item.get("title") or item.get("raw_title") or "")
    if not title:
        return item

    kind="series" if item.get("media_type")=="tv" else "movie"
    client=node_client.client()

    # 1. Direct by-title lookup. OMDb officially supports t=<title>, type=...
    r=await client.get(
        "https://www.omdbapi.com/",
        params={"apikey":key,"t":title,"type":kind,"r":"json"},
        headers={"Accept":"application/json"},
        timeout=8,
    )
    r.raise_for_status()
    payload=r.json()

    def acceptable(candidate_title:str)->bool:
        a=_title_key(title)
        b=_title_key(candidate_title)
        if not a or not b:
            return False
        if a==b:
            return True
        # Tolerate a leading English article and punctuation/spacing differences,
        # but not arbitrary fuzzy matches.
        strip_the=lambda x: x[3:] if x.startswith("the") else x
        return strip_the(a)==strip_the(b)

    if str(payload.get("Response","False")).lower()=="true" and acceptable(payload.get("Title","")):
        enriched=dict(item)
        enriched["title"]=clean_title(payload.get("Title") or title) or title
        year=_year(str(payload.get("Year") or ""))
        if year:
            enriched["year"]=year
            enriched["year_source"]="OMDb"
        enriched["enriched_by"]="OMDb"
        enriched["imdb_id"]=payload.get("imdbID")
        return enriched

    # 2. Search fallback for titles that OMDb does not resolve directly.
    r=await client.get(
        "https://www.omdbapi.com/",
        params={"apikey":key,"s":title,"type":kind,"r":"json"},
        headers={"Accept":"application/json"},
        timeout=8,
    )
    r.raise_for_status()
    payload=r.json()
    if str(payload.get("Response","False")).lower()!="true":
        return item

    candidates=[x for x in (payload.get("Search") or []) if acceptable(x.get("Title",""))]
    if not candidates:
        return item

    chosen=candidates[0]
    enriched=dict(item)
    enriched["title"]=clean_title(chosen.get("Title") or title) or title
    year=_year(str(chosen.get("Year") or ""))
    if year:
        enriched["year"]=year
        enriched["year_source"]="OMDb"
    enriched["enriched_by"]="OMDb"
    enriched["imdb_id"]=chosen.get("imdbID")
    return enriched


async def lookup(code:str)->dict:
    barcode=normalise(code)
    if not barcode:return {"found":False,"barcode":code,"matches":[],"error":"That barcode does not look valid"}
    matches=[];errors=[]
    for name,fn in (("MusicBrainz",_musicbrainz),("Google Books",_books),("UPCitemdb",_upc)):
        try:matches.extend(await fn(barcode))
        except httpx.HTTPStatusError as e:errors.append(f"{name}: HTTP {e.response.status_code}")
        except Exception as e:errors.append(f"{name}: {type(e).__name__}")
    unique={}
    for x in matches:
        key=(re.sub(r"\W+","",x["title"].lower()),x.get("year"),x["media_type"])
        if key not in unique or x["confidence"]>unique[key]["confidence"]:unique[key]=x
    matches=sorted(unique.values(),key=lambda x:-x["confidence"])[:12]

    # Secondary film/TV metadata pass. One failed enrichment must never hide the
    # UPC result; the editable fields still receive the cleaned provider data.
    enriched=[]
    for item in matches:
        try:
            enriched.append(await _omdb_enrich(item))
        except httpx.HTTPStatusError as e:
            errors.append(f"OMDb: HTTP {e.response.status_code}")
            enriched.append(item)
        except Exception as e:
            errors.append(f"OMDb: {type(e).__name__}")
            enriched.append(item)
    matches=enriched

    return {"found":bool(matches),"barcode":barcode,"matches":matches,"errors":errors,
            "error":None if matches else "No metadata match was found"}
