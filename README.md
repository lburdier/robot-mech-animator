# Robot Mech Animator

Extension Blender (5.0 et plus) pour animer un robot mech bipède armé de deux tourelles :

- **un rig propre** :
  - jambes en IK avec **vérins hydrauliques** qui coulissent ;
  - pieds, orteils et **volet mécanique avant du pied** ;
  - corps et **tête** (torse) ;
  - épaules, tourelles et gatlings ;
  - limites de rotation mesurées pour éviter toute collision ;
- **une locomotion à pieds plantés** : le robot marche, sprinte, fait des pas de côté et **tourne sur place à 360°**, et ses jambes suivent sans glisser ;
- **un pilotage en direct** à la **manette PS4** (DualShock 4) ou au **clavier** (AZERTY ou QWERTY), avec **enregistrement des keyframes** en temps réel ;
- **un chemin prédéfini** à suivre, et **une IA sur navmesh** qui patrouille, fait des allers-retours, erre ou rejoint une cible ;
- **des effets de tir** :
  - flashs de bouche des gatlings ;
  - **roquettes** avec flamme du moteur ;
  - **traînée de fumée volumétrique** ;
  - explosion à l'impact ;
- **5 presets de tourelles**, plus une salve de roquettes, et un preset de marche ;
- **une peinture militaire PBR** construite à partir des textures existantes : camouflage (forêt, désert, urbain, olive ou hiver) sur les panneaux, **canons en métal nu**, **visière noire brillante**, tiges de vérins chromées, usure et saleté ;
- **des marquages (decals)** : numéros, insigne, « DANGER », « NO STEP », plaques techniques, bandes de danger, étiquettes de munitions ;
- **tout est annulable** : chemin et IA (Échap), prise au pilotage (Share / Suppr), et un bouton « Annuler la dernière action ».

> Le modèle 3D du robot n'est **pas** inclus dans ce dépôt, car c'est un modèle tiers.
> Le script `tools/build_rig.py` construit le rig à partir de votre fichier `.blend`.

---

## Installation

1. Téléchargez `robot_mech_animator-x.y.z.zip` depuis la page **Releases**.
2. Dans Blender, ouvrez **Édition › Préférences › Get Extensions**, puis le menu **⌄** en haut à droite, et choisissez **Install from Disk…**.
3. Sélectionnez le zip. L'onglet **Robot** apparaît dans la barre latérale de la vue 3D (touche `N`).

### Mises à jour automatiques (depuis la 1.2.0)

L'addon vérifie lui-même s'il existe une nouvelle release sur GitHub :

- au démarrage de Blender, au plus une fois toutes les 24 h (réglable) ;
- quand une version est disponible, un bloc **Mise à jour disponible** apparaît en haut de l'onglet **Robot**, avec **Installer**, **Notes** et **Ignorer** ;
- l'option **Installer automatiquement** télécharge et installe la nouvelle version sans rien demander ;
- avant l'installation, le zip est contrôlé : taille, empreinte SHA-256 publiée par GitHub, identifiant et version de l'extension.

Les réglages se trouvent dans **Préférences › Extensions › Robot Mech Animator**, avec un bouton **Vérifier**. L'addon ne se connecte que si **Préférences › Système › Réseau › Autoriser l'accès en ligne** est activé.

Autre méthode, entièrement gérée par Blender : dans **Préférences › Get Extensions › Repositories**, cliquez **+ › Add Remote Repository** et indiquez l'URL suivante, puis installez l'addon depuis ce dépôt :

```
https://github.com/lburdier/robot-mech-animator/releases/latest/download/index.json
```

Dans ce cas, désinstallez d'abord la version installée depuis le disque, pour qu'il n'y ait pas deux copies actives.

## Construire le rig

```bash
blender -b --factory-startup --disable-autoexec "Rig robot.blend" \
        --python tools/build_rig.py -- "Robot_Rig.blend"
# optionnel : peinture militaire (WOODLAND, DESERT, URBAN, OD, ARCTIC)
blender -b Robot_Rig.blend --python tools/apply_paint.py -- WOODLAND "Robot_Rig_militaire.blend"   # + marquages (--no-decals pour les omettre)
```

Le script :

- supprime l'ancienne armature cassée et les 162 empties FBX vides ;
- réaligne la jambe droite sur le miroir exact de la gauche, et pose le robot au sol ;
- **corrige l'axe de rotation des gatlings**, qui était incliné dans le mauvais sens : les canons « tournaient de travers » ;
- découpe chaque bras en trois parties : moyeu fixé au torse, **épaule** (hausse) et **canon** (cap, hausse, recul) ;
- **mesure sur la géométrie réelle** les angles atteignables sans collision, et les enregistre dans le rig ;
- reprend les os proposés par la version « Rig robot (1) » : épaules, volets avant des pieds, chevilles ;
- ajoute les **vérins des jambes** : la tige vise son ancrage pendant que le genou plie ;
- ajoute les points d'émission des effets : bouches des gatlings et tubes lance-roquettes.

## Contrôleurs

| Contrôleur | Rôle | Canaux |
|---|---|---|
| `root` | position et orientation globales | loc, rot Z |
| `body` | centre de gravité (hauteur, roulis, inclinaison) | loc, rot |
| `torso` | **tête** : tourner (±58°) et incliner (±12°) | rot |
| `shoulder.L` / `.R` | épaule : hausse (+24° / −20°) | rot X |
| `turret.L` / `.R` | canon : hausse (+20° / −35°), cap (34° vers l'intérieur, 18° vers l'extérieur), recul | rot X/Z, loc Y |
| `gatling.L` / `.R` | rotation des canons | rot Y |
| `aim_target` | cible, avec le curseur « Suivi de la cible » | loc |
| `foot_ik.L` / `.R` | pieds en IK | loc, rot |
| `knee_pole.L` / `.R` | orientation des genoux | loc |
| `toe_front.*` / `toe_back.*` | orteils | rot X |
| `toe_flap.L` / `.R` | volet mécanique avant du pied | rot X |

Ce qui est automatique :

- les **vérins** (`piston_rod.*`) suivent les genoux ;
- `muzzle.*` et `rocket_port_1/2.*` servent aux effets ;
- tous ces os sont rangés dans le calque masqué « Mecanique ».

## Pilotage : manette PS4 ou clavier

Branchez la manette en USB ou appairez-la en Bluetooth, puis cliquez **Piloter le robot**. Sous Windows, aucun logiciel tiers n'est nécessaire : l'addon lit la manette via WinMM/DirectInput. Si vous utilisez DS4Windows ou Steam Input, choisissez **XInput** dans les préférences. Le clavier fonctionne seul ou en même temps que la manette.

| Manette | Action | Clavier |
|---|---|---|
| Stick gauche | marcher, tourner (sur place : 360°) | Z/W S, Q/A D |
| **L3 (clic)** | **sprint** | Maj |
| Carré + stick gauche | pas de côté | Alt ou E + Q/D |
| Stick droit | viser avec la ou les tourelles | Flèches ou I J K L |
| L1 + stick droit | tourner la tête | U O, Y H |
| Croix directionnelle | tête : tourner ← →, incliner ↑ ↓ | pavé 4 6 8 2 |
| R2 | tir gatling : rotation, recul et **flash** | Espace |
| R1 | **roquette** (maintenu : salve) | F |
| L2 | s'accroupir | Ctrl ou C |
| Triangle | tourelle gauche, droite ou les deux | T |
| R3 | recentrer les tourelles | V |
| **Share** | **annuler la prise en cours** | Suppr |
| **Croix** | **démarrer ou arrêter l'enregistrement** | R |
| Rond | pose de repos | Retour arrière |
| Options / PS | quitter | Échap |

Pendant l'enregistrement, une pose est inscrite à chaque image, au rythme réel de la scène. Les flashs de tir sont enregistrés avec elle, et les roquettes partent dans la timeline. À l'arrêt, les clés redondantes sont supprimées avec une tolérance garantie.

## Chemin et IA (navmesh)

Panneau **Chemin et IA** :

1. **Chemin prédéfini** :
   - cliquez **En S**, **Boucle** ou **Ligne** pour créer une courbe devant le robot ;
   - modifiez-la (Tab) ;
   - cliquez **Suivre le chemin** : le robot la suit en marchant, avec les clés posées à partir de l'image courante.
2. **Navmesh** :
   - choisissez le **Sol** et une collection d'**Obstacles** ;
   - cliquez **Créer le navmesh** : une grille praticable est créée, qui exclut les pentes trop fortes et les obstacles, élargis du rayon du robot.
   - Vous pouvez aussi utiliser votre propre maillage comme navmesh.
3. **IA** :
   - placez des points de patrouille (curseur 3D, puis **Ajouter un point**) ;
   - choisissez un comportement : *patrouille en boucle*, *aller-retour*, *errance* ou *aller à la cible* ;
   - cliquez **Lancer l'IA**.
   - Le robot calcule son chemin (A* et lissage), contourne les obstacles, s'arrête à chaque point et observe en tournant la tête et les tourelles.

## Effets et peinture

- **Effets de tir** :
  - **Salve de roquettes** : les roquettes partent des tubes, la flamme et la lumière du moteur s'allument, une traînée de fumée volumétrique se forme (dérive, montée, turbulence, dissipation) et une explosion se produit à l'impact sur le décor.
  - Rien n'est à précalculer : tout est en keyframes et en Geometry Nodes, visible en rendu EEVEE et Cycles.
  - **Effacer les effets** supprime les roquettes, les fumées et les explosions.
- **Effets en pilotage** : sans enregistrement, les roquettes partent quand même, en direct (trajectoire, fumée et explosion animées en temps réel, puis supprimées). Pendant l'enregistrement, elles sont posées dans la timeline. Sans cible ni obstacle, une roquette explose en fin de portée (60 m par défaut).
- **Peinture militaire PBR** (panneau **Peinture et marquages**) : un groupe de nœuds reprend la couleur, le métal, la rugosité et le vernis de chaque matériau.
  - Les panneaux peints à l'origine reçoivent le camouflage, modulé par la luminosité des textures (détails, arêtes et voyants restent visibles).
  - Les parties sombres deviennent du métal bronzé ; les gatlings et canons sont en **métal nu** (option : tourelles entières ou aucune).
  - La **visière** est trouvée automatiquement et devient noire brillante, avec vernis ; les tiges de vérins sont chromées.
  - **Usure** (rayures et éclats qui montrent l'acier) et **Saleté** (crasse dans les creux, coulures).
  - **Retirer** restaure exactement les matériaux d'origine.
- **Marquages** : **Poser les marquages** place 24 decals (jeu complet) ou quelques-uns (discret) sur les zones planes : numéro 07 ou 12, insigne, « DANGER – ROCKET EXHAUST AREA », « NO STEP », « MK-VII », plaque constructeur, « HYDRAULIC 3000 PSI », bandes de danger sur les volets des pieds, étiquettes de munitions et d'inspection. Chaque decal suit l'os de sa pièce et épouse la surface (Shrinkwrap) ; vous pouvez le déplacer à la main. L'atlas est empaqueté dans le .blend.

## Annuler

| Mode | Pendant | Après |
|---|---|---|
| Suivre le chemin / Lancer l'IA | barre de progression ; **Échap**, clic droit ou bouton **Annuler** : tout est remis comme avant | bouton **Annuler : …** en haut de l'onglet Robot |
| Pilotage manette / clavier | **Share** / **Suppr** ou bouton **Annuler la prise** : la prise en cours est jetée ; bouton **Arrêter** pour quitter | bouton **Annuler : Prise au pilotage** |

L'annulation restaure l'animation, la pose, la plage d'images et supprime les roquettes de la prise. Ctrl+Z de Blender fonctionne aussi.

## Tests

```bash
blender -b --factory-startup --disable-autoexec Robot_Rig.blend --python tests/test_addon.py
# mise à jour de bout en bout dans un profil vierge (ancien zip, puis zip plus récent)
BLENDER_USER_RESOURCES=/tmp/profil blender -b --factory-startup --online-mode     --python tests/test_updater.py -- ancien.zip nouveau.zip
```

Les 143 vérifications couvrent :

- le rig : axe des gatlings, limites des tourelles, vérins, volet du pied ;
- la marche et la locomotion : portée de l'IK, pieds sans glisse, rotation à 360°, sprint, pas de côté ;
- la tête, les tirs et les roquettes ;
- l'enregistrement ;
- le clavier ;
- le chemin, le navmesh et l'IA (contournement d'un mur, points visités) ;
- la peinture PBR (visière, métal nu, retrait exact), les marquages, l'interface ;
- les roquettes en direct et l'explosion en fin de portée ;
- l'annulation (chemin, calcul interrompu, prise au pilotage).

Ces tests ont été passés sous Blender 5.0 et 5.3.

## Licence

GPL-3.0-or-later. Voir `LICENSE`.
