"""Diagnostic Microsoft Graph : verifie tes droits SANS creer d'App registration.

Utilise le client public first-party "Microsoft Graph Command Line Tools"
(client_id 14d82eec-204b-4c2f-b7e8-296a70dab67e), pre-enregistre dans tous les
tenants Microsoft 365. Authentification par device code (tu ouvres une URL et
saisis un code dans TON navigateur, avec TON compte : aucun mot de passe ne
passe par le script).

Lancer :  python check_graph.py
"""
from __future__ import annotations

import sys

import msal
import requests

# Client public first-party Microsoft (Graph CLI) : aucune app a creer.
PUBLIC_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
AUTHORITY = "https://login.microsoftonline.com/organizations"
SCOPES = ["Mail.Read", "Files.Read.All", "Sites.Read.All", "User.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"


def main() -> int:
    app = msal.PublicClientApplication(PUBLIC_CLIENT_ID, authority=AUTHORITY)

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("ERREUR : impossible de demarrer le device flow.")
        print(flow.get("error_description", flow))
        return 1

    print("\n" + "=" * 60)
    print("  AUTHENTIFICATION MICROSOFT (ton compte Bouygues)")
    print("=" * 60)
    print(flow["message"])
    print("=" * 60)
    print("En attente de ta connexion dans le navigateur...\n")
    sys.stdout.flush()

    result = app.acquire_token_by_device_flow(flow)  # bloque jusqu'a login/timeout

    if "access_token" not in result:
        print("\n[X] Authentification/consentement REFUSE.")
        print("    Raison :", result.get("error"))
        print("   ", result.get("error_description", "").split("\n")[0])
        print("\n=> Tu n'as PAS les droits de consentir seul.")
        print("   Il faudra un consentement admin OU une App registration dediee.")
        return 2

    token = result["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("\n[OK] Authentifie avec succes !\n")

    caps = {}

    # 1) Identite
    try:
        me = requests.get(f"{GRAPH}/me", headers=headers, timeout=20).json()
        print(f"  Compte : {me.get('displayName')} <{me.get('userPrincipalName')}>")
    except Exception as exc:  # noqa: BLE001
        print("  /me KO :", exc)

    # 2) Lecture des mails
    try:
        r = requests.get(f"{GRAPH}/me/messages?$top=1&$select=subject,receivedDateTime",
                         headers=headers, timeout=20)
        caps["Mail.Read"] = r.status_code == 200
        if r.status_code == 200:
            v = r.json().get("value", [])
            ex = v[0].get("subject") if v else "(boite vide)"
            print(f"  [OK] Mails accessibles - dernier sujet : {ex}")
        else:
            print(f"  [X] Mails : HTTP {r.status_code} - {r.text[:120]}")
    except Exception as exc:  # noqa: BLE001
        caps["Mail.Read"] = False
        print("  [X] Mails KO :", exc)

    # 3) Recherche de fichiers (syntheses Copilot) via Search API
    try:
        body = {"requests": [{"entityTypes": ["driveItem"],
                              "query": {"queryString": "compte rendu OR recap OR summary"},
                              "from": 0, "size": 3}]}
        r = requests.post(f"{GRAPH}/search/query", headers=headers, json=body, timeout=20)
        caps["Files/Search"] = r.status_code == 200
        if r.status_code == 200:
            hits = 0
            for c in r.json().get("value", []):
                for h in c.get("hitsContainers", []):
                    hits += h.get("total", 0)
            print(f"  [OK] Recherche fichiers OK - ~{hits} resultat(s) 'compte rendu/recap'")
        else:
            print(f"  [X] Recherche fichiers : HTTP {r.status_code} - {r.text[:120]}")
    except Exception as exc:  # noqa: BLE001
        caps["Files/Search"] = False
        print("  [X] Recherche fichiers KO :", exc)

    print("\n" + "-" * 60)
    ok = all(caps.values()) and caps
    if ok:
        print("  RESULTAT : tu as les droits necessaires. On peut brancher Graph !")
        print(f"  Client public utilisable : {PUBLIC_CLIENT_ID}")
    else:
        print("  RESULTAT : acces partiel. Details ci-dessus.")
    print("-" * 60)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
