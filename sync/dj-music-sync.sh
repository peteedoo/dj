#!/usr/bin/env bash
# Syncs curated DJ music from NAS to Music_Studio SSD
# Add/remove folders from the FOLDERS array to manage what gets copied

SRC="/Volumes/homelab/media/music"
DEST="/Volumes/Music_Studio/DJ Music"
LOG="/tmp/dj-music-sync.log"

if [ ! -d "$SRC" ]; then
  echo "$(date): NAS not mounted at $SRC — skipping" >> "$LOG"
  exit 0
fi

if [ ! -d "$DEST" ]; then
  mkdir -p "$DEST"
fi

FOLDERS=(
  # Hip-Hop / Rap
  "A\$AP Rocky - Don't Be Dumb (FLAC)   16Bit 44.1kHz Rap  Hip-Hop  (2026)  Beats⭐"
  "Common"
  "Common & Pete Rock"
  "Common feat. Brittany Howard"
  "Common feat. De La Soul"
  "Common feat. Elijah Blake"
  "Common feat. John Legend"
  "Common feat. Lily Allen"
  "Common feat. PJ"
  "Common feat. PJ & Stevie Wonder"
  "Common feat. Snoh Aalegra & Dreezy"
  "Common feat. Syd & Elena"
  "Common Sense"
  "DMX"
  "DJ Quik"
  "Eminem"
  "Eminem - Discography 1996-2020 [FLAC] vtwin88cube"
  "Heavy Crownz - Trench Baby Turned Farmer Rap  Hip-Hop  (2026) 320_kbps Beats⭐"
  "Hoodrich Keem - Slurred Words  Rap  Hip-Hop  (2026) 320_kbps Beats⭐"
  "Immortal Technique - The 3rd World"
  "J Dilla (2006) HHIR - Donuts [The Samples]"
  "Jeru the Damaja"
  "Joey Bada\$\$ - 1999"
  "Kanser - It Wrote Itself"
  "Kanser - Self Titled"
  "Kanser-Future_Retro_Legacy-2008-FTD"
  "More_Than_Lights_(Kanser)-The_Electric_Prescription_For_All_Your_Funky_Illz-2009-FTD"
  "Kanye West - Bully (First Pressing CD Edition) Rap  Hip-Hop  (2026) 320_kbps Beats⭐"
  "Lil Wayne, Wiz Khalifa & Imagine Dragons Feat. Logic, Ty Dolla \$ign & X Ambassadors"
  "Lil' Wayne"
  "MAC MILLER - DISCOGRAPHY [CHANNEL NEO]"
  "Mac Lethal"
  "The Commission Feat. Jay-Z & The Notorious B.I.G"
  "The Doppelgangaz"
  "The Notorious B.I.G. Feat. Bone Thugs-N-Harmony & Twista"
  "The Notorious B.I.G. Feat. T.I"
  "50 Cent - Best Of 50 Cent (2017) [FLAC] 88"
  "VA - I Believe This to Be The Supreme Formula  Hip-Hop  Poetry (2026) 320_kbps Beats⭐"
  # Electronic / Beats
  "Boards of Canada"
  "Disclosure Feat. AlunaGeorge"
  "Klangkarussell Feat. Will Heard"
  "Pastel Ghost"
  "Philanthrope"
  "Stan Forebee"
  "Nymano j'san"
  "SwuM Benno"
  "Subtronics"
  "Trivecta"
  "The Flight"
  "SEXY-SYNTHESIZER"
  # Funk / Soul
  "Ambassadors Of Funk featuring M. C. Mario"
  "Bionic Boogie"
  "Donny Hathaway"
  "Fun Lovin' Criminals - Come Find Yourself (1996)"
  "Sly Johnson - Mister Johnson (2026 Soul Funk RnB) [Flac 24-44]"
  "Vulfpeck"
)

echo "$(date): Starting DJ music sync" >> "$LOG"
synced=0
skipped=0

for folder in "${FOLDERS[@]}"; do
  src_path="$SRC/$folder"
  if [ -d "$src_path" ]; then
    rsync -a --ignore-existing "$src_path" "$DEST/" >> "$LOG" 2>&1
    echo "  synced: $folder" >> "$LOG"
    ((synced++))
  else
    ((skipped++))
  fi
done

echo "$(date): Done — $synced synced, $skipped not found on NAS" >> "$LOG"
