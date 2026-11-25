#!/usr/bin/env python3
# Proton Drive BFS-Scanner, gehaertet gegen Re-Traversierung geteilter Ordner.
# Fix ggue. v2: Visited-Set per Node-UID (ein kanonischer Pfad je Knoten; geteilte/
# verlinkte Ordner werden NICHT mehrfach traversiert/gezaehlt), Retry bei Listing-
# Fehlern, hoeheres Tiefenlimit + explizite INCOMPLETE-Markierung statt stillem Weglassen.
# Ausgabeformat identisch zu v2 (F/D-Zeilen), damit Map-Tooling kompatibel bleibt.
#
# Hintergrund: v2 fehlte ein Visited-Set. Proton-Drive-Knoten, die ueber mehrere
# Elternpfade erreichbar sind (geteilte/verlinkte Ordner, gleiche UID), wurden mehrfach
# traversiert -> Ueberzaehlung (Scan lief ueber die wahre Gesamtzahl hinaus). Dedupe per
# UID stellt sicher, dass jeder Knoten genau einmal unter seinem kanonischen (zuerst, d.h.
# flachsten) Pfad erscheint. Echte Kopien (unterschiedliche UIDs, gleiche sha1) bleiben
# erhalten -- nur identische Knoten werden kollabiert.
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
BIN="/opt/proton-drive-cli/0.9.0/proton-drive"
ROOT=sys.argv[1]
WORKERS=int(sys.argv[2]) if len(sys.argv)>2 else 5
MAX_DEPTH=int(sys.argv[3]) if len(sys.argv)>3 else 25
RETRIES=3

def list_dir(item):
    abspath, rel = item
    last="unknown"
    for attempt in range(RETRIES):
        try:
            p=subprocess.run([BIN,"filesystem","list","-j",abspath],
                             capture_output=True,text=True,timeout=120)
        except Exception as e:
            last="run:"+type(e).__name__
        else:
            if p.returncode==0:
                try: return (rel, json.loads(p.stdout), None)
                except Exception as e: last="parse:"+str(e)[:50]
            else:
                last="rc=%d:%s"%(p.returncode,(p.stderr or '').strip()[:80])
        time.sleep(1.5*(attempt+1))
    return (rel, None, last)

def gv(e,k):
    v=e.get(k)
    return v.get("value","") if isinstance(v,dict) else (v or "")

seen=set()  # Node-UIDs, die bereits ausgegeben wurden
level=[(ROOT,"")]; depth=0; nfold=nfile=ndup=incomplete=0
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    while level and depth<MAX_DEPTH:
        nextlevel=[]
        for rel, entries, err in ex.map(list_dir, level):
            if entries is None:
                print("ERR\t%s\t%s"%(rel,err)); incomplete+=1; continue
            for e in sorted(entries, key=lambda x:(x.get("type")!="folder", gv(x,"name").lower())):
                name=gv(e,"name"); typ=e.get("type","?"); uid=e.get("uid","")
                if uid and uid in seen:
                    ndup+=1; continue  # geteilter/verlinkter Knoten -> nicht doppelt, nicht re-traversieren
                if uid: seen.add(uid)
                childrel=rel+"/"+name
                shared="SHARED-URL" if e.get("isSharedByUrl") else ""
                if typ=="folder":
                    print("D\t%s\t%s\t\t\t\t\t%s"%(childrel,uid,shared)); nfold+=1
                    nextlevel.append((ROOT+childrel, childrel))
                else:
                    ar=e.get("activeRevision") or {}
                    size=e.get("totalStorageSize") or ar.get("storageSize") or ""
                    sha1=(ar.get("claimedDigests") or {}).get("sha1","")
                    mt=(e.get("modificationTime") or "")[:19]
                    media=(e.get("mediaType") or "").split("/")[-1][:28]
                    print("F\t%s\t%s\t%s\t%s\t%s\t%s\t%s"%(childrel,uid,size,sha1,mt,media,shared)); nfile+=1
        sys.stdout.flush(); level=nextlevel; depth+=1
    if level:  # Tiefenlimit erreicht, Rest explizit als unvollstaendig markieren
        for abspath, rel in level:
            print("INCOMPLETE\t%s\tdepth-limit"%rel); incomplete+=1
print("# gesamt=%d Ordner=%d Dateien=%d dup-uids-uebersprungen=%d incomplete=%d tiefe=%d"
      %(nfold+nfile,nfold,nfile,ndup,incomplete,depth))
