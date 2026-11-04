# Construire l'application : .exe (Windows) et .apk (Android)

Une seule base de code Flask + SQLite pour tous les appareils.

| Appareil | Livrable | Fonctionnement |
|---|---|---|
| PC Windows | `GestionEtudiants.exe` | Contient Python + serveur + interface. Double-clic : une fenetre s'ouvre. Donnees dans `%APPDATA%\GestionEtudiants`. |
| Telephone / tablette Android (7.0+) | `GestionEtudiants.apk` | Application qui affiche l'interface du serveur. A la 1re ouverture, saisir l'adresse du serveur. |
| iPhone / iPad, tout navigateur | URL du serveur | Installation PWA (voir README.md, section 4). |

## Utiliser PC + telephones ensemble (meme base de donnees)

1. Sur le PC, lancer `GestionEtudiants.exe --lan` (raccourci : ajouter `--lan` dans "Cible"),
   ou mettre `{"lan": true}` dans `%APPDATA%\GestionEtudiants\config.json`.
   La fenetre/console indique l'adresse (ex. `http://192.168.1.20:5000`) et la **cle API**
   (egalement dans `config.json`). Autoriser le pare-feu Windows pour les reseaux prives.
2. Sur le telephone (meme Wi-Fi) : ouvrir l'APK, saisir `http://192.168.1.20:5000`,
   puis saisir la cle API une seule fois dans l'interface.
3. Pour un acces hors du reseau local : deployer avec Docker (README.md, section 3) derriere HTTPS
   et saisir `https://votre-domaine` dans l'APK.

Sans `--lan`, le .exe n'ecoute que sur la machine locale (127.0.0.1) et fonctionne seul, sans cle a saisir.

## Methode 1 - GitHub Actions (recommandee, rien a installer)

1. Creer un depot GitHub et y envoyer ce dossier (`git init && git add . && git commit -m init && git push`).
2. Onglet **Actions** -> workflow **Build EXE + APK** -> *Run workflow*.
3. A la fin (~5-8 min), telecharger les artefacts `GestionEtudiants-windows-exe` et
   `GestionEtudiants-android-apk`. Avec un tag (`git tag v5.0 && git push --tags`) les deux
   fichiers sont aussi publies dans *Releases*.

Le workflow teste que le .exe demarre et repond sur `/health` avant de le publier.

## Methode 2 - Construire soi-meme

**.exe** (sur un PC Windows avec Python 3.10+ ; PyInstaller ne sait pas compiler pour Windows depuis Linux/macOS) :
double-cliquer `desktop\build_exe.bat` -> `dist\GestionEtudiants.exe`.

**.apk** (JDK 17 + Android Studio, ou SDK Android + Gradle 8.9) :
ouvrir le dossier `android/` dans Android Studio -> *Build > Build APK(s)*,
ou en ligne de commande : `cd android && gradle assembleRelease`
-> `android/app/build/outputs/apk/release/app-release.apk`.

## Installer l'APK

Copier le fichier sur le telephone, l'ouvrir, autoriser "Installer des applications inconnues"
pour l'application utilisee (Fichiers, Chrome...). L'APK est signe avec une cle de debug :
suffisant pour une distribution interne ; pour le Play Store il faut une cle de signature propre.

## Remarque Windows

Le .exe n'etant pas signe numeriquement, Windows SmartScreen peut afficher
"Windows a protege votre ordinateur" : *Informations complementaires* -> *Executer quand meme*.
La fenetre native utilise Microsoft WebView2 (deja present sur Windows 10/11 a jour) ;
sinon l'application s'ouvre automatiquement dans le navigateur.
