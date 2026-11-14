# Robot Mech Animator

Extension Blender (5.0 et plus) pour animer un robot mech bipède avec deux tourelles :

- un **rig propre** : jambes en IK à trois segments, pieds et orteils, corps, torse, deux tourelles, gatlings et une cible de visée ;
- un **preset de marche** procédural, en boucle infinie ou posé en clés sur la timeline ;
- **5 presets de tourelles** : balayage, visée, rafale gatling, tir lourd et patrouille ;
- le **pilotage en direct avec une manette PS4** (DualShock 4), avec **enregistrement des keyframes** en temps réel.

> Le modèle 3D du robot n'est **pas** inclus dans ce dépôt (modèle tiers). Le script
> `tools/build_rig.py` construit le rig à partir de votre fichier `.blend`.

---

## Installation

1. Téléchargez `robot_mech_animator-x.y.z.zip` depuis la page **Releases**.
2. Dans Blender, ouvrez **Édition › Préférences › Get Extensions**, puis le menu **⌄** en haut à droite, et choisissez **Install from Disk…**.
3. Sélectionnez le zip. L'onglet **Robot** apparaît dans la barre latérale de la vue 3D (touche `N`).

## Construire le rig

```bash
blender -b --factory-startup --disable-autoexec "Rig robot.blend" \
        --python tools/build_rig.py -- "Robot_Rig.blend"
```

Le script :

- supprime l'ancienne armature cassée et les 162 empties FBX vides ;
- réaligne la jambe droite sur le miroir exact de la gauche ;
- pose le robot au sol (z = 0) ;
- crée l'armature `RIG_Robot` avec ses contraintes, ses formes de contrôleurs, ses collections d'os et ses couleurs.

## Contrôleurs

| Contrôleur | Rôle | Canaux |
|---|---|---|
| `root` | position et orientation globales | loc, rot Z |
| `body` | centre de gravité (hauteur, roulis, inclinaison) | loc, rot |
| `torso` | rotation du torse (±75°) | rot |
| `turret.L` / `turret.R` | **tourelles** : hausse (X), cap (Z), recul (loc Y) | rot X/Z, loc Y |
| `gatling.L` / `gatling.R` | rotation des canons | rot Y |
| `aim_target` | cible : réglez le curseur « Suivi de la cible » (0→1) pour que les tourelles la visent | loc |
| `foot_ik.L` / `foot_ik.R` | pieds en IK | loc, rot |
| `knee_pole.L` / `knee_pole.R` | orientation des genoux | loc |
| `toe_front.*` / `toe_back.*` | orteils | rot X |

Les rotations des tourelles sont limitées (cap ±28°, hausse −22°/+40°, recul 0–0,3 m) par des contraintes qui s'appliquent aussi pendant la manipulation.

## Presets

- **Marche** : longueur et hauteur de pas, appui au sol, rebond, balancement, roulis, contre-rotation du torse, bascule des pieds, flexion des orteils, sur place, marche arrière, démarrage et arrêt progressifs.
- **Tourelles** (gauche, droite ou les deux), insérés à la frame courante :
  - *Balayage* ;
  - *Visée* (avec dépassement puis stabilisation) ;
  - *Rafale gatling* (montée en régime, recul, vibrations) ;
  - *Tir lourd* ;
  - *Patrouille* (aléatoire, avec une graine).

## Pilotage à la manette PS4

Branchez la manette en USB ou appairez-la en Bluetooth, puis cliquez **Piloter le robot**. Aucun logiciel tiers n'est nécessaire sous Windows : l'addon lit la manette via WinMM/DirectInput. Si vous utilisez DS4Windows ou Steam Input, choisissez **XInput** dans les préférences. Sous Linux et macOS, le pilote `pygame` est expérimental.

| Manette | Action | Clavier |
|---|---|---|
| Stick gauche | marcher / tourner | Flèches |
| Stick droit | viser avec la ou les tourelles sélectionnées | I J K L |
| R2 (analogique) | tirer : rotation des gatlings et recul | Espace |
| L2 (analogique) | s'accroupir | C |
| L1 / R1 / Triangle | tourelle gauche / droite / les deux | T |
| Croix directionnelle ← → | rotation du torse | |
| Croix directionnelle ↑ ↓ | hauteur du corps | |
| **Croix** | **démarrer / arrêter l'enregistrement** | R |
| Rond | pose de repos | Retour arrière |
| Carré | marche sur place | G |
| L3 / R3 | course / recentrer les tourelles | F / V |
| Options / PS | quitter | Échap |

Pendant l'enregistrement, une pose est inscrite à chaque image, au rythme réel de la scène (réglable : une clé toutes les N images, ralenti). À l'arrêt, les clés redondantes sont supprimées avec une tolérance garantie.

Pour vérifier le mappage, utilisez le bouton **Tester la manette**, qui affiche en direct les axes et les boutons. Les axes sont réassignables dans les préférences de l'extension.

## Tests

```bash
blender -b --factory-startup --disable-autoexec Robot_Rig.blend --python tests/test_addon.py
```

Les 56 vérifications couvrent :

- le sens des tourelles et le respect de leurs limites ;
- la portée de l'IK et l'absence de pieds qui glissent ou passent sous le sol ;
- l'avancée du root ;
- les 5 presets × 3 côtés ;
- le pilotage simulé et l'enregistrement en temps réel ;
- les pilotes de manette et l'interface.

Ces tests ont été passés sous Blender 5.0 et 5.3.

## Licence

GPL-3.0-or-later. Voir `LICENSE`.
