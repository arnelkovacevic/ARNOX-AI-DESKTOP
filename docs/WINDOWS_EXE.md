# ARNOX — uputstvo za Windows `.exe`

Ovo uputstvo opisuje kako napraviti `ARNOX.exe` na Windows računaru i kako ga instalirati na drugi Windows računar.

> Važno: `.exe` se mora praviti **na Windowsu**. PyInstaller ne pravi Windows aplikaciju s macOS-a.

---

## 1. Šta treba prije početka

- Windows 10 ili 11, 64-bit.
- **Python 3.12** s [python.org](https://www.python.org/downloads/). Na prvom ekranu instalacije obavezno uključi **"Add python.exe to PATH"**.
- **Git** (opcionalno, za preuzimanje projekta): [git-scm.com](https://git-scm.com/download/win).
- **Gemini API ključ** iz Google AI Studija.
- **Gmail OAuth JSON** (Desktop app klijent) iz Google Cloud, ako želiš Gmail i Calendar.

---

## 2. Preuzimanje projekta

Otvori **cmd** ili **PowerShell** i pokreni:

```bat
git clone https://github.com/arnelkovacevic/ARNOX-AI-DESKTOP.git
cd ARNOX-AI-DESKTOP
```

Ili preuzmi ZIP sa GitHuba, raspakuj ga i uđi u folder.

---

## 3. Instalacija zavisnosti

U folderu projekta pokreni:

```bat
scripts\setup_jarvis.bat
```

Skripta:
- kreira `.venv` (virtuelno okruženje),
- instalira sve pakete iz `requirements.txt`,
- kreira `.env` iz `.env.example`, ako ne postoji.

---

## 4. Podešavanje `.env`

Otvori `.env` u Notepadu i upiši svoj ključ:

```
GEMINI_API_KEY="tvoj_ključ"
GEMINI_VOICE_NAME="puck"
```

Ako koristiš Gmail preko fajla, dodaj i punu putanju do JSON-a:

```
GMAIL_OAUTH_CLIENT_FILE="C:\Users\ime\Documents\client_secret_....json"
```

Nikada ne šalji `.env` ili JSON fajl nikome i ne stavljaj ih na GitHub.

---

## 5. Instalacija PyInstallera

```bat
.venv\Scripts\python -m pip install pyinstaller
```

---

## 6. Pravljenje `.exe`

Iz foldera projekta pokreni:

```bat
.venv\Scripts\pyinstaller JARVIS.spec --noconfirm
```

Kad se završi, rezultat je:

```
dist\ARNOX\ARNOX.exe
```

Build traje nekoliko minuta. Folder `dist\ARNOX\` je nekoliko stotina MB, što je normalno (PyQt6, OpenCV i drugi paketi).

---

## 7. Pokretanje

1. Kopiraj svoj **`.env`** u folder `dist\ARNOX\`, pored `ARNOX.exe`.
2. Ako koristiš Gmail preko fajla, JSON može biti bilo gdje, ali putanja u `.env` mora biti tačna.
3. Dvaput klikni `ARNOX.exe`.

Aplikacija pri prvom pokretanju kreira `memory\` i `config\` foldere pored `.exe`-a.

---

## 8. Instalacija na drugi Windows računar

- Kopiraj **cijeli folder** `dist\ARNOX\` (ne samo `.exe`).
- Python nije potreban na tom računaru.
- Kopiraj `.env` i, ako treba, Gmail JSON.
- Pri prvom pokretanju Windows SmartScreen može prikazati upozorenje, jer `.exe` nije potpisan. Klikni **More info → Run anyway**.

### Gmail na drugom računaru

Gmail token se čuva u **Windows Credential Manageru**, pa se ne prenosi s prvog računara. Na novom računaru ponovo poveži Gmail:
1. Prevuci JSON u ARNOX prozor ili reci/upiši: *"Connect Gmail using this file."*
2. U Google stranici izaberi svoj Gmail nalog i potvrdi dozvole.

---

## 9. Šta radi, a šta ne radi na Windowsu

| Funkcija | Radi na Windowsu |
|---|---|
| Glas i chat (Gemini) | Da |
| Gmail (čitanje, nacrti, smeće, slanje uz potvrdu) | Da |
| Google Calendar (agenda, dodavanje, izmjena, brisanje) | Da |
| Web pretraga i duboko istraživanje | Da |
| Prezentacije (`.pptx`) | Da |
| Apple Mail, iMessage | Ne (macOS only) |
| Funkcije koje koriste macOS dozvole i AppleScript | Ne |

---

## 10. Česti problemi

**`.exe` se zatvara odmah.**
Pokreni ga iz cmd-a da vidiš grešku:
```bat
cd dist\ARNOX
ARNOX.exe
```

**`GEMINI_API_KEY environment variable not set`.**
`.env` nije pored `ARNOX.exe`. Kopiraj ga u `dist\ARNOX\`.

**Nedostaje modul (`ModuleNotFoundError` ili `No module named ...`).**
PyInstaller nije uključio neki dinamički import, jer ARNOX učitava neke akcije preko `importlib`. Dodaj modul u `hiddenimports` u `JARVIS.spec`, pa ponovo pokreni build:
```bat
.venv\Scripts\pyinstaller JARVIS.spec --noconfirm
```

**Prompt izgleda kao rezervni.**
Provjeri da `dist\ARNOX\_internal\core\prompt.txt` postoji. Ako ne postoji, ponovo napravi build s najnovijim `JARVIS.spec`.

**Gmail traži ponovnu prijavu.**
Normalno je na novom računaru. Vidi korak 8.

**Antivirus blokira `.exe`.**
Nepotpisani PyInstaller programi se često označavaju kao sumnjivi. Dodaj `dist\ARNOX\` u izuzetke antivirusa ili pokreni iz foldera koji je već dozvoljen.

---

## 11. Ažuriranje

Kad promijeniš kod:
1. Povuci najnoviju verziju (`git pull`) ili kopiraj izmijenjene fajlove.
2. Ponovo pokreni `.venv\Scripts\pyinstaller JARVIS.spec --noconfirm`.
3. Zamijeni stari `dist\ARNOX\` novim, ali zadrži svoj `.env`.
