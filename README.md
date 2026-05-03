# 🚀 Setup-Anleitung für Einsteiger (Fork)

> Dieser Abschnitt wurde im Fork ergänzt und ist **nicht Teil des Originals von Reloisback**. Die ursprüngliche README beginnt weiter unten.

Diese Anleitung führt Schritt für Schritt durch die Installation des Bots auf einem leeren Rechner. Sie setzt **keine Vorkenntnisse** voraus.

## 1. Voraussetzungen

| Was | Warum |
|-----|-------|
| Computer mit Windows, macOS oder Linux | Bot läuft 24/7 stabiler auf einem Server, lässt sich aber lokal testen |
| Internetverbindung | Bot kommuniziert mit Discord und der WOS-API |
| Discord-Account mit eigenem Server | Zum Einladen und Testen des Bots |
| ca. 30 Minuten Zeit | Für die Erstinstallation |

## 2. Python 3.12.4 installieren

Der Bot benötigt **exakt Python 3.12.4** (neuere oder ältere Versionen können Probleme verursachen).

### Windows

1. Öffne https://www.python.org/downloads/release/python-3124/
2. Scrolle nach unten zu **Files** und lade **Windows installer (64-bit)** herunter.
3. Starte den Installer. **Wichtig:** Setze unten den Haken bei **„Add python.exe to PATH"** bevor du auf *Install Now* klickst.
4. Prüfe die Installation: Drücke `Win + R`, tippe `cmd`, drücke Enter. Im schwarzen Fenster eingeben:
   ```
   python --version
   ```
   Die Ausgabe muss `Python 3.12.4` lauten.

### macOS

1. Installiere [Homebrew](https://brew.sh/) (falls noch nicht vorhanden):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
2. Python installieren:
   ```bash
   brew install python@3.12
   ```
3. Prüfen:
   ```bash
   python3.12 --version
   ```

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip git
python3.12 --version
```

## 3. Git installieren

- **Windows:** https://git-scm.com/download/win herunterladen und mit Standardeinstellungen installieren.
- **macOS:** `brew install git` (oder bereits via Xcode Command Line Tools vorhanden).
- **Linux:** `sudo apt install git`

Prüfen mit:
```bash
git --version
```

## 4. Discord-Bot erstellen und Token erzeugen

1. Öffne https://discord.com/developers/applications
2. Klicke oben rechts auf **New Application**, gib einen Namen ein, akzeptiere die Bedingungen.
3. Im linken Menü **Bot** auswählen → **Add Bot** → **Yes, do it!**
4. Unter **Token** auf **Reset Token** klicken und den angezeigten Token **sofort kopieren** (er wird nur einmal gezeigt).
5. Auf derselben Seite folgende Schalter aktivieren:
   - **Presence Intent**
   - **Server Members Intent**
   - **Message Content Intent**
6. Speichern.

### Bot auf den eigenen Discord-Server einladen

1. Im linken Menü **OAuth2 → URL Generator**.
2. Bei **Scopes** auswählen: `bot` und `applications.commands`.
3. Bei **Bot Permissions** mindestens auswählen:
   - `Manage Channels`, `View Channels`, `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `Use Slash Commands`, `Manage Messages`, `Mention Everyone`
   (oder zur Vereinfachung **Administrator** — nur für Tests sinnvoll)
4. Die unten generierte URL im Browser öffnen, Server auswählen, bestätigen.

## 5. Repository klonen

Im Terminal (Eingabeaufforderung bei Windows) in den Ordner wechseln, in dem der Bot liegen soll, dann:

```bash
git clone https://github.com/wosbot-4-dontgetscammed/Whiteout-Survival-Discord-Bot.git
cd Whiteout-Survival-Discord-Bot
```

## 6. Virtuelle Python-Umgebung anlegen

Eine virtuelle Umgebung (venv) trennt die Bot-Pakete vom Systempython und verhindert Konflikte.

### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

### macOS / Linux

```bash
python3.12 -m venv venv
source venv/bin/activate
```

Nach erfolgreicher Aktivierung steht `(venv)` am Anfang der Eingabezeile.

## 7. Abhängigkeiten installieren

Der Bot installiert benötigte Pakete normalerweise beim ersten Start automatisch. Wer es manuell vorab erledigen möchte:

```bash
pip install --upgrade pip
pip install discord.py colorama requests aiohttp python-dotenv aiohttp-socks pytz pyzipper
```

## 8. `.env`-Datei konfigurieren

1. Vorlage kopieren:
   - **Windows:** `copy .env.example .env`
   - **macOS/Linux:** `cp .env.example .env`
2. Datei `.env` mit einem Texteditor (Notepad, VS Code, nano …) öffnen und folgende Werte eintragen:

   ```ini
   BOT_TOKEN=hier_dein_discord_bot_token
   WOS_ENCRYPT_KEY=tB87#kPtkxqOS2
   WOS_TEST_PLAYER_ID=244886619
   WOSLAND_API_KEY=optional_falls_vorhanden
   WOSLAND_BACKUP_API_KEY=optional_falls_vorhanden
   WOSLAND_BACKUP_API_URL=https://wosland.com/apidc/backup_api/backup_api.php
   ```

   - `BOT_TOKEN` ist **Pflicht** (aus Schritt 4).
   - `WOS_ENCRYPT_KEY` ist der bekannte WOS-Standardwert.
   - `WOS_TEST_PLAYER_ID` ist eine beliebige gültige Spieler-ID zum Testen der API.
   - Die `WOSLAND_*`-Werte sind optional und nur nötig, wenn du das WOSLand-Backup-Feature nutzt.

3. **Wichtig:** Datei `.env` niemals teilen oder ins Repository committen — sie enthält dein Geheimnis. Das mitgelieferte `.gitignore` schützt sie bereits.

## 9. Bot starten

Mit aktiviertem venv:

```bash
python main.py
```

Beim ersten Start:
- Fehlende Pakete werden automatisch nachinstalliert.
- Datenbanken werden in `db/` angelegt.
- Logs landen in `log/bot.log`.

Wenn der Bot online ist, in deinem Discord-Server `/settings` eingeben — der erste Nutzer, der diesen Befehl ausführt, wird automatisch Hauptadministrator.

## 10. Bot dauerhaft laufen lassen (optional)

Beim Schließen des Terminals beendet sich der Bot. Für Dauerbetrieb gibt es mehrere Optionen:

- **Linux mit systemd:** Service-Unit anlegen (Anleitung siehe Wiki).
- **Windows:** [NSSM](https://nssm.cc/) als Dienst registrieren.
- **macOS:** `launchd`-Plist anlegen oder einfach in einem `tmux`/`screen` laufen lassen.
- **Cloud:** Günstige VPS-Hoster (Hetzner, Netcup, Contabo …) ab ca. 4 €/Monat.

## 11. Bot aktualisieren

```bash
git pull
source venv/bin/activate    # macOS/Linux
# bzw. venv\Scripts\activate auf Windows
pip install --upgrade pip
python main.py
```

Updates aus dem Original-Repo von Reloisback ziehen (einmalig einrichten):
```bash
git remote add upstream https://github.com/Reloisback/Whiteout-Survival-Discord-Bot.git
git fetch upstream
git merge upstream/main
```

## 12. Probleme & Fehlersuche

| Symptom | Ursache / Lösung |
|---------|------------------|
| `python` nicht gefunden | Bei Windows wurde der PATH-Haken im Installer vergessen → Python neu installieren |
| `ModuleNotFoundError` | venv nicht aktiviert oder Pakete fehlen → Schritt 6 + 7 wiederholen |
| Bot startet, reagiert aber nicht auf Befehle | Intents in Discord Developer Portal nicht aktiviert (Schritt 4.5) |
| `Improper token` | `BOT_TOKEN` in `.env` falsch eingetragen oder mit Anführungszeichen umgeben — diese entfernen |
| API-Fehler `40103` (CAPTCHA) | Normales Verhalten, Bot wiederholt automatisch |
| Bot stoppt nach Terminal-Schließen | Siehe Schritt 10 |

Bei weiteren Fragen Issue im Fork-Repo öffnen oder im offiziellen Discord von Reloisback nachfragen.

---

# 📜 Originale README von Reloisback

A long time ago, while working on this project, my car caught fire, causing severe burns to my body. Before that, the people I trusted to hand over the project to, and those I gave authority to, betrayed me. While I was in the hospital receiving burn treatment, they stole all my data and projects. I was the one who originally started the entire bot and Discord project—they only stole my data. Soon, I will report everything they are selling for money to the WOS team. We are currently in contact, and I will release the entire project’s source code completely free of charge. You will achieve nothing with the project and members you stole from me. Our work with the authorities to fix all the vulnerabilities in White of Survival is ongoing. My warning to you at this stage: never invest your money into projects that will inevitably be shut down—always choose free projects.
# Important information (01.08.2025)
I don't have a Discord channel right now.
I only have an email address: usabsz@gmail.com
Wos Land does not belong to me.  
Wos Land is a project that was stolen from me.  
https://discord.gg/SSQBFvk73V
If you look back, you will see that I was the one who started the Wos Land and Discord bot project.  
A few months ago, I suffered severe burns on my body due to a gas explosion and became unable to do anything. The people I trusted and gave authority to took advantage of this situation and took everything I owned.
While I was fighting for my life in the hospital, they took away my entire project.

I advise you not to trust any other project besides this one.  
Once the skin on my body has regenerated, I will continue developing the project.

# V4 Mass Update List (25.02.2025)

Since the release of V4, we've implemented numerous changes and improvements. Here's a comprehensive overview of the latest updates:

## 🛠️ Bug Fixes & Improvements
- 90% of reported issues on Discord have been resolved
- Continuous maintenance and bug fixing
- Implementing user-requested custom features

## 🎁 Gift Code System
- Introduced new Gift Code API system
- Shared gift code database for easier management
- Automatic expired code cleanup
- Alliance auto-gift code usage feature
  - Automatically detects and applies codes for all members
  - Customizable periodic alliance checks

## 📢 Notification System
- Unlimited custom notifications
- Flexible timing configuration
- Multiple notification intervals
- Example scenario:
  ```
  Event time: 18:00 UTC
  Notifications at:
  - 17:40 (20 minutes before)
  - 17:50 (10 minutes before)
  - 17:55 (5 minutes before)
  - 18:00 (Event start)
  ```
- Web interface for notification management
  - Visit: https://discord.gg/SSQBFvk73V

## 💾 Backup System
- Automatic database backup
- Secure encrypted backups (.zip format)
- Personal encryption key system
- Private backup link generation
- Enhanced data privacy
  - Only member IDs are stored
  - Encrypted access

## 🆔 ID Channel System
- Automatic alliance member addition
- Discord channel integration
- Duplicate entry prevention
- Comprehensive logging system

## ⚙️ Additional Features
- Alliance Control Messages toggle in Bot Operations menu
- Customizable progress notifications
- Enhanced user experience

## 🌟 Support & Community
Join our Discord community for:
- Direct support
- Feature requests
- Updates and announcements
- [Join Discord Server
](https://discord.gg/SSQBFvk73V)
## 🔄 Ongoing Development
We continue to:
- Implement user feedback
- Fix reported issues
- Add new features
- Improve system stability

Have a great day! 

---
*For more information and support, visit our [Discord Server https://discord.gg/SSQBFvk73V

# V4 UPDATED

- Let's talk a little bit about V4
  - Scroll down to the bottom of the page to read the functions of the Menus
- First of all, those who will set up a discord bot for the first time can watch the youtube video by clicking [FULL HERE](https://www.youtube.com/watch?v=SwbOOij8wFY)
  
  - In version V4 we have 2 codes
1. Our code is /settings. As soon as you use this in the first installation, you will be set as the main administrator
2. Our code is /w command, this command is for ID Check

- In V4, python library installation, updates, etc. everything is automated
- We even have a system that checks for expired gift codes with hidden checks and deletes them from the database
- There are many features here that I cannot fully explain
  
  ##### REMEMBER USE PYTHON VERSION 3.12.4
  
  # About me: 

# White of Survival Discord Bot

## 👋 Welcome!
Thank you for using our Discord bot. This bot is designed to help manage your White of Survival alliance efficiently and effectively.

## 🆘 Support Information

### Need Help?
If you're experiencing any issues or need assistance with the bot, we're here to help!

### 📞 Contact Methods
- **Discord Server:** [Join Our Community](https://discord.gg/SSQBFvk73V))
- **Developer:** Reloisback
- **Direct Support:** Feel free to message me on Discord

## 💝 Support the Project

### Always Free
This bot was created and published by Reloisback and will **ALWAYS BE FREE**. We believe in providing quality tools accessible to everyone.

### ☕ Buy Me a Coffee
If you'd like to support the development:
- [Buy Me a Coffee](https://www.buymeacoffee.com/reloisback)
- Your support helps maintain and improve the bot

## 🔓 Open Source
Our bot's source code is 100% open source. We believe in transparency and community-driven development.

## 💌 Final Note
Thank you for being part of our community! Your support and feedback help make this bot better for everyone.

Feel free to reach out anytime - we're always happy to help!

---
*Made with ❤️ by Reloisback*


# 👨‍💻 About the Developer

## Personal Introduction
I'm Umut, a 27-year-old developer specializing in Python and PHP. While I used to be an avid gamer, my responsibilities as a family provider have shifted my priorities, leaving limited time for gaming.

## 🤖 Bot's Journey
White of Survival bot started as a fun project for my own alliance. Upon realizing there wasn't anything similar available, I decided to develop it further and share it with the community. You're currently experiencing Version 4, following successful releases of V1, V2, and V3.

The development process has been intense, ranging from 1-2 hours some days to marathon 14-15 hour coding sessions.

## 💭 Why Free?
I'm often asked why I keep this bot free. The answer is simple: accessibility. If monetized, the user base would shrink from thousands to perhaps just 10-15 users. Having experienced financial constraints myself, I understand the importance of making useful tools available to everyone, regardless of their financial situation.

## 🤝 Support & Development
For those who can and wish to support the project, you can use the [☕ Buy me a coffee](https://www.buymeacoffee.com/reloisback) link. These contributions help cover development costs:
- Proxy servers
- Testing environments
- Server maintenance
- Development tools

## 💝 Final Words
To those unable to provide financial support - thank you for using the bot! Support has never been and will never be mandatory. This project will remain free forever.

I love this community and thank you all for being part of this journey. ❤️

---

### Quick Links
- [Discord Server](https://discord.gg/SSQBFvk73V))
- [Support Page](https://www.buymeacoffee.com/reloisback)
- Discord: Reloisback

*Made with passion and dedication for the White of Survival community* ❤️


# WOS Discord Bot V4 Documentation

## Main Menu Buttons

### 🏰 Alliance Operations
- Add, remove and edit alliances
- Seeing Existing Alliances

### 👥 Member Operations
- Add, delete and view alliance members
- Member transfer from Alliance to Alliance

### ⚙️ Bot Operations
- Adding, deleting and viewing admins
- Alliance-specific admin authorization and deletion
- Transferring old V3 and V2 database information 
- Checking Bot Updates
- Log System (The log channel you select to see the members added and deleted by administrators)
### 📜 Alliance History Menu
- View nickname and furnace level history of any member or alliance
### 🆘 Support Operations
- Help and developer information
- Direct contact options

### 🔧 Other Features
- Opens additional features menu
- Reserved for future updates
# Button Descriptions
## 🏰 Alliance Operations Menu

### ➕ Add Alliance
- Pressing this Button prompts you for 3 pieces of information
- Alliance name and Interval time (Interval time is how many minutes it will check automatically, if you type 0, there will be no automatic check)
- It then prompts you to select a channel and shows both the automatically redeemed gift code information and the names and oven levels that change under automatic alliance control.

### 🗑️ Delete Alliance
- Deletes all information and members of your selected alliance

### ✏️ Edit Alliance
- Allows you to change the name, control time or control channel of the alliance you added

### 👀 View Alliances
- Shows your alliance lists, how many members it has and how often it is checked


## 👥 Member Operations Menu

### ➕ Add Members
- Used to add members to your alliance
- When you press the button, it asks you for 2 pieces of information:
  - First it asks which alliance you want to add members to
  - Then you will be asked to enter the IDs of the players in the window that appears, (id1,id2,id3 you can add in bulk)
  - If members are added, you will be able to see them moment by moment
- It records the details of the added members here, i.e. their logs: `log/add_memberlog.txt`
### ➖ Remove Member
- Press this and it asks you to choose an alliance
- Then it shows the members of the alliance, you can either delete them all or select 1 member and delete it
### 📋 View Members
- When you press it, it asks you to choose an alliance,
- Then shows the members of the alliance



## 🤖 Bot Operations Menu
 > 90% of the buttons in this menu can only be used by the owner of the bot

### ➕ Add Admin

- This feature adds admin to your bot. 
- After pressing it, it asks you to tag the admin
    - The administrators you add cannot access all settings.
    - They can only see and manage alliances in the discord they are in.
    - They can also see specially authorized alliances

### ➖ Remove Admin

- Press this and it will show you the list of attached admins
- When you select the administrator, it shows you the details and deletes the administrator if you confirm

### 👥 View Administrators

- Shows your admin list and displays the current authorizations of the admins

### 🔗 Assign Alliance to Admin

- Allows you to assign a custom alliance management to the admins you add
- This is done in different discord server so that the admins you want can see the other alliances

### ➖ Delete Admin Permissions

- This feature allows you to delete the alliance management that you have specifically assigned to the admins


### 🔄 Transfer Old Database

- For those who use V2 or V3, it is made to transfer the old database to the V4 database
- If you put the V2 or V3 database in the file location where main.py is located and then press this button and select the correct version, your members, your members' changes gift codes will be transferred automatically

### 🔄 Check for Updates

- If you check if there is a new version of the bot
- It will tell you what has changed, if anything

### 📋 Log System

- This button allows you to select the admin log management channel
- It will tell you to choose an alliance, and after choosing an alliance, it will tell you to choose the discord channel.
- Shows the actions of the administrators who add or delete members on that channel

## 🎁 Gift Code Operations


### 🎫 Create Gift Code

- This button allows you to add gift code manually

### 📋 List Gift Codes

- This button lists the attached gift codes

### ❌ Delete Gift Code

- This button allows you to delete gift code

### 📢 Gift Code Channel

- This button checks gift code
- It asks you to choose an alliance and then asks you to choose a discord channel
- Checks the giftcode written by each user in this discord channel
- If it is a valid giftcode, it adds it to the database
- If the alliance's automatic gift code usage option is active, it will be used for the whole alliance
- You can add the channel of your choice by following the messages of the gift code channel from the WOS discord
- Automatically detects gift codes here

### 🗑️ Delete Gift Channel

- Deletes the channel that controls the written gift codes

### ⚙️ Auto Gift Settings

- When you press this button it asks you to choose an alliance
- If the alliance is approved and the alliance has a Gift Code Channel
- It always uses the successful gift codes it captures there for the approved alliance

### 🎯 Use Gift Code for Alliance

- Prompts you to choose an alliance and a gift code, then redeems the gift code for everyone in that alliance


## 📝 Alliance History Menu

### 🔥 Furnace Changes

- Pressing this button opens the alliance list
- After selecting an alliance, the alliance member list will appear, displaying the history of Furnace Changes of the selected member
- Or you can enter a number between 1 and 24 hours to see all alliance members who made changes within this interval

### 📝 Nickname Changes

- Pressing this button opens the alliance list
- After selecting an alliance, the alliance member list will appear, displaying the history of Nickname Changes of the selected member
- Or you can enter a number between 1 and 24 hours to see all alliance members who made changes within this interval


# General Features


## Everything in one place

- In the V4 version, Bot has integrated every feature into a single menu
- More stable and faster
- More details are included
- Details are given in the embed message for all your transactions

## Automatic Update System

- We have activated automatic update in version V4
- User will receive a warning when starting the bot if there is an update
- Within this warning you will see what has changed
- If he agrees, these changes will be implemented automatically
### Real-time Progress Tracking
- Live updates via embeds
- Color-coded status indicators:
  - 🔵 Blue: In Progress
  - 🟠 Orange: Rate Limited
  - 🟢 Green: Completed
  - 🔴 Red: Error

### Error Handling
- Rate limit detection
- API error management
- Database error handling
- User-friendly error messages

### Logging System
- Automatic log directory creation
- Detailed operation logs
- Timestamp tracking
- Success/Failure records

### Database Management
- Multiple SQLite databases:
  - alliance.sqlite: Alliance data
  - users.sqlite: Member information
  - settings.sqlite: Bot configuration
  - giftcode.sqlite: Gift code records



# White of Survival Discord Bot - Screenshots

## Bot Interface Screenshots

![Screenshot1](pictures/Screenshot_1.png)
![Screenshot2](pictures/Screenshot_2.png)
![Screenshot3](pictures/Screenshot_3.png)
![Screenshot4](pictures/Screenshot_4.png)
![Screenshot5](pictures/Screenshot_5.png)
![Screenshot6](pictures/Screenshot_6.png)
![Screenshot7](pictures/Screenshot_7.png)
![Screenshot8](pictures/Screenshot_8.png)
![Screenshot9](pictures/Screenshot_9.png)
![Screenshot10](pictures/Screenshot_10.png)
![Screenshot11](pictures/Screenshot_11.png)

---





