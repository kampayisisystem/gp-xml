"""GP Kompozit bayi panelinden XML'i indirip repoya kaydeder, Telegram'a bildirir."""
import os
import glob
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone, timedelta

import requests

BASE = "https://bayi.gpkompozit.com.tr"
LOGIN_URL = BASE + "/auth/signin"
XML_URL = BASE + "/index/xml"
CIKTI = "urunler.xml"

KULLANICI = os.environ.get("GP_KULLANICI", "")
SIFRE = os.environ.get("GP_SIFRE", "")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")

TR = timezone(timedelta(hours=3))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


def telegram(mesaj):
    print(mesaj)
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": mesaj}, timeout=20)
    except Exception as e:
        print("Telegram hatası:", e)


def xml_mi(icerik):
    return icerik.lstrip()[:200].lower().startswith((b"<?xml", b"<rss", b"<urun", b"<products", b"<root"))


def urun_sayisi(icerik):
    root = ET.fromstring(icerik)
    # Ürün listesi: en sık tekrar eden alt etiket (kök altı veya bir alt seviye)
    for katman in (list(root), [c for x in root for c in x]):
        if katman:
            etiket, adet = Counter(c.tag for c in katman).most_common(1)[0]
            if adet > 1:
                return adet
    return len(list(root))


def calis():
    if not (KULLANICI and SIFRE):
        raise RuntimeError("GP_KULLANICI / GP_SIFRE secret eksik")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})

    # 1) Giriş
    s.get(BASE + "/auth/login", timeout=60)
    r = s.post(LOGIN_URL, data={"redirecturl": "", "customer": "",
                                "KULLANICI_ADI": KULLANICI, "SIFRE": SIFRE},
               timeout=60, allow_redirects=True)
    if r.status_code != 200 or "/auth/login" in r.url or 'name="SIFRE"' in r.text:
        raise RuntimeError(f"Giriş başarısız (HTTP {r.status_code}, {r.url})")

    # 2) XML
    r = s.get(XML_URL, timeout=180)
    r.raise_for_status()
    icerik = r.content
    if not xml_mi(icerik):
        # Sayfa XML yerine HTML döndüyse içindeki ilk xml linkini dene
        linkler = re.findall(r'href="([^"]*xml[^"]*)"', r.text, re.I)
        linkler = [l for l in linkler if l.rstrip("/") != "/index/xml"]
        if not linkler:
            raise RuntimeError("XML sayfası açıldı ama XML içeriği bulunamadı")
        link = linkler[0] if linkler[0].startswith("http") else BASE + "/" + linkler[0].lstrip("/")
        r = s.get(link, timeout=180)
        r.raise_for_status()
        icerik = r.content

    adet = urun_sayisi(icerik)  # bozuk XML ise burada hata verir
    if adet == 0:
        raise RuntimeError("XML boş geldi (0 ürün)")

    # 3) Kaydet (değişmediyse dokunma)
    eski = open(CIKTI, "rb").read() if os.path.exists(CIKTI) else b""
    if eski:
        eski_adet = urun_sayisi(eski)
        if adet < eski_adet * 0.5:
            raise RuntimeError(f"Ürün sayısı yarıdan fazla düştü ({eski_adet} → {adet}), eski XML korundu")
    degisti = icerik != eski
    if degisti:
        with open(CIKTI, "wb") as f:
            f.write(icerik)

    # Arşiv: son 5 XML (orijinal byte'larıyla)
    os.makedirs("arsiv", exist_ok=True)
    shutil.copyfile(CIKTI, "arsiv/urunler_" + datetime.now(TR).strftime("%Y-%m-%d_%H%M") + ".xml")
    for fazla in sorted(glob.glob("arsiv/urunler_*.xml"))[:-5]:
        os.remove(fazla)

    with open("son_calisma.txt", "w") as f:
        f.write(datetime.now(TR).strftime("%Y-%m-%d %H:%M") + f" | {adet} ürün\n")

    durum = "güncellendi" if degisti else "değişiklik yok"
    telegram(f"✅ GP Kompozit XML {durum} — {adet} ürün")


if __name__ == "__main__":
    try:
        calis()
    except Exception as e:
        telegram(f"❌ GP Kompozit XML hatası: {e}\nEski XML yerinde kaldı.")
        with open("son_calisma.txt", "w") as f:
            f.write(datetime.now(TR).strftime("%Y-%m-%d %H:%M") + f" | HATA: {e}\n")
        sys.exit(1)
