---
name: molecule-editing
description: '**EDITING SKILL** — Modify a molecule in place from atoms the user selected in the viewer: rebuild a fragment as a benzene ring, a vinyl, or any standard group, and publish the edited structure with the rest of the complex intact. Replaces exactly the atoms selected — never more, never fewer, and never by rewriting selection.json. USE FOR: "change this into a benzene ring"; "make this a vinyl"; replacing a selected fragment with a standard group; editing a ligand the user is pointing at in the viewer; any request to alter part of a structure while keeping the rest. DO NOT USE FOR: designing a new molecule from scratch (use molecule-generation or pocket-molecule-design); docking (use molecular-docking); publishing an unmodified structure (use structure-viewer). Requires a selection in the viewer — read selection.json first. INVOKES: file system tools, the structure-viewer publisher.'
---

# Molecule Editing

Change part of a structure the user selected in the viewer, and publish the
result with everything else intact.

**Prerequisite:** the user must have selected atoms in the viewer. Read
`webapp/static/selection.json` first — if `atoms` is empty, ask them to switch
the viewer to **Atom** mode and select the atoms to change.

## When to Use

- "Change this into a benzene ring"
- "Replace this fragment with a ring"
- "Make these atoms a phenyl"
- "Make this a vinyl" / "change this to an ethyl"
- Any request to alter part of a structure while keeping the rest

## The one rule

**Edit in place. Never publish the edited fragment alone.**

A request to change a fragment into a benzene ring is a request about that
fragment, not about the whole structure — but the user is looking at the whole
structure, and publishing only the ring leaves them staring at a benzene
floating in space with the protein it came from gone. The selected atoms are
replaced; every other atom, chain and bond is copied through untouched.

## The other rule

**Replace exactly what was selected. Nothing more.**

The selection is the user's statement of what they want changed, and it is the
only one they made. A selection of one atom means one atom. Widening it to two
because two "makes more chemical sense" replaces something they did not ask
about, and the result is a structure they did not ask for — with no way to see
that it happened.

This is not hypothetical. Asked to change a vinyl's terminal carbon into a
benzene, the selection held that one carbon; the edit was made on the carbon
and its neighbour instead, on the reasoning that a single carbon "cannot usefully
become a ring". The user's answer was that they had wanted exactly that, and the
substitution they got was not the one they asked for.

A selection that looks chemically odd is still the selection. If it genuinely
cannot be carried out — the atoms are not bonded to the rest, say — the script
says so and stops. That is the only grounds for not doing it.

**Never write `selection.json` to change what is being edited.** It is the
viewer's file, written by the viewer, and it is the record of what the user
clicked. Overwriting it to widen or narrow a selection destroys the only
evidence of their intent, and the next read of it reports the edit as faithful
when it was not. If the selection needs to be different, the user changes it in
the viewer.

## Replace with a group

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
python3 $BB/skills/molecule-editing/scripts/replace_with_group.py \
  --structure <the PDB the selection was made on> \
  --group benzene --out runs/edited.pdb
```

The script reads `selection.json` itself. RDKit does the chemistry:

1. **Resolve the selection.** The viewer records the PDB serial for every atom
   it selects, so this is a lookup. The 3Dmol index is deliberately not used —
   3Dmol reorders atoms when it builds a model, so its index is not a position
   in the file and cannot be walked back to a serial. A selection written
   before serials were recorded falls back to the label.
2. **Read the structure** as an RWMol with `sanitize=False` — a PDB off disk
   routinely has valence errors and RDKit refuses the whole molecule over one
   of them, while the edit only needs the graph and the coordinates.
3. **Find the attachment points** — the heavy atoms outside the selection that
   the removed group was bonded to. These are what the new group reconnects to.
   Hydrogens are skipped: a PDB read with `proximityBonding` bonds every
   hydrogen it finds, so the hydrogens sitting on the selected group's own
   atoms look like surviving neighbours, and bonding to one leaves the real
   attachment dangling.
4. **Delete the selected atoms** with `RWMol.RemoveAtom`, capturing the
   direction each broken bond pointed first, because removal renumbers
   everything after it. Hydrogens the deletion orphaned are removed too — they
   survive it, bonded to nothing, floating exactly where the new group is about
   to go.
5. **Place the group with `GetAlignmentTransform`.** A reference is built from
   the group's own MMFF geometry, expressed in a target frame: the centre one
   bond length plus the group's radius beyond the attachment, along the
   direction the removed group occupied, with atom 0 facing back at the
   attachment and the group's plane lying in the removed group's plane. RDKit
   solves the rigid transform that puts **every** heavy atom onto it, and
   `TransformMol` applies it. No centroid is guessed, no rotation is searched.

   Mapping all the heavy atoms, not just the first two, is what fixes the
   orientation. A two-atom reference pins the bond's position and direction
   but leaves the group free to spin about that bond, and RDKit picks an
   arbitrary angle — one that can swing a far side of the group back onto the
   attachment, so the viewer draws a bond that was never asked for. With the
   full group mapped the alignment RMSD is 0.000 and the geometry is exact.

   The bond to the attachment is sized from covalent radii of the two elements
   involved, not from a constant. A vinyl's C-S is 1.81 A and a benzene's C-C
   1.39 A, and one number for both puts the group's first atom at the wrong
   distance — which drags its hydrogens into the attachment.
6. **Bond it in** with `AddBond`, and write the PDB with CONECT records from
   the bond list so the viewer draws the group and its bond to the rest.

The group's geometry comes from an MMFF optimisation of its SMILES, so a
thiazole's C-S is 1.72 A and its C-N 1.32 A rather than a regular polygon's
approximation.

**Groups available:** `benzene`, `thiazole`, `pyridine`, `imidazole`, `furan`,
`thiophene`, `pyrrole`, `oxazole`, `isoxazole`, `vinyl`, `ethyl`, `ethynyl`,
`hydroxyl`, `amino`, `methyl`, `nitro`, `carboxyl`, `acetyl`, `cyano`,
`aldehyde`, `methoxy`, `sulfhydryl`.

Rings and chains go through the same path. The only thing that differs between
them is how many hydrogens the template carries, and that is read off the
template rather than assumed — a vinyl's terminal carbon needs two, its middle
carbon one, a benzene's carbons one each. Hard-coding "one H per non-bonding
atom" is what made the ring-only version unable to build a vinyl.

Everything else in the structure is copied through untouched.

**The result is a connected substitution.** The group bonds to whatever the
removed group bonded to, at a real bond length. If the selection spanned two
fragments the group can only reach one side, and the script says so rather than
drawing a line the geometry does not support.

## Publish the result

An edit is a task like any other, and it belongs on the status bar. Without
`--status` the structure appears in the viewer and the status bar keeps showing
whatever ran last — so the user sees a new tab and no explanation of where it
came from, and the edit leaves no trace in the task list to come back to.

Report the edit starting, then publish with it finished:

```bash
BB="${BB:-$HOME/biobench}"
[ -d "$BB/skills" ] || BB=$(cd ~ 2>/dev/null; for d in biobench BioBench; do [ -d "$HOME/$d/skills" ] && echo "$HOME/$d" && break; done)
PUB=$BB/skills/structure-viewer/scripts/publish.py

# Before the edit
python3 $PUB --status --state running --step "基团替换" --index 1 --total 2 \
  --skill molecule-editing --message "正在把选中的原子替换为苯环"

# After the edit — the structure and the finished step in one call
python3 $PUB --structure runs/edited.pdb --name <name> \
  --source molecule-editing --job <run-id> \
  --roles "A=receptor,B=receptor,L=ligand" \
  --notes "what was replaced, and what was kept" \
  --metrics '{"bond_to_attachment_A": {"label": "与连接点键长", "value": 1.52, "hint": "Å"}, "ring_cc_A": {"label": "环内 C-C", "value": 1.39, "hint": "Å"}}' \
  --status --state done --step "基团替换" --index 2 --total 2 \
  --skill molecule-editing --message "苯环已重建，结构完整保留"
```

`--roles` must name every chain, including the ones that were not touched —
the viewer reads roles to label the chain list, and an unlabelled chain shows
up as a bare "Chain A".

`--message` is capped at 60 characters; a longer one is accepted with a
warning. Say what changed, not how.

`--metrics` is worth filling in for an edit: the bond length to the attachment
and the group's own internal bond lengths are the numbers that say whether the
substitution is sound, and they are already computed by the time the edit is
done.

## Traps this skill has already fallen into

Each of these produced a structure that looked plausible and was wrong. They are
listed because the failure is silent — nothing errors, the PDB writes, the viewer
draws it.

The ones about formats and tools rather than chemistry — `proximityBonding`
bonding a protein side chain to a ligand, a protein written as HETATM, the
fixed-column layouts — are in `_shared/silent-failures.md`, because every skill
that touches a structure shares them. What follows is specific to placing a
group.

**Two-atom alignment leaves the group free to spin.** Mapping only the group's
first two atoms pins the bond's position and direction but not the rotation about
it, and RDKit picks an arbitrary angle. One of those angles swings a far side of
the ring back onto the attachment, and the viewer draws a bond that was never
asked for. Map every heavy atom; the RMSD goes to 0.000 and the orientation is
exact.

**A constant bond length is wrong for every group but one.** `BOND = 1.39` is an
aromatic C-C. Used for a vinyl's bond to sulfur it is 0.34 Å short, which drags
the group's hydrogens into the attachment — they end up 1.25 Å from an atom they
are not bonded to, where a real `S-CH=CH2` puts them at 2.40 Å. Size the bond from
the covalent radii of the two elements actually involved.

**The deletion orphans hydrogens.** Those same hydrogens are not in the selection,
so removing the group does not remove them. They survive, bonded to nothing,
floating exactly where the new group is about to be placed — and the new group's
atoms land on top of them. Remove any hydrogen left with no heavy-atom neighbour.

**The template's atom 0 carries a hydrogen that must go.** The template is the
unsubstituted group, so `c1ccccc1`'s atom 0 has the hydrogen that position
normally bears — and that position is precisely where the bond to the attachment
now goes. Left in place it sits on the attachment at 0.72 Å. Delete it after the
merge.

**`AddConformer` on a copy appends, it does not replace.** `Chem.RWMol(template)`
brings the template's conformer along, so adding a second one leaves
`GetConformer` returning the untransformed first. The alignment then solves
against the wrong coordinates and reports a perfect RMSD for a transform that was
never needed. `RemoveConformer(0)` first.

**Rotating about the bond axis does not move a hydrogen away from the
attachment.** The attachment is on that axis, so the hydrogen's distance to it is
invariant under the rotation — the search finds every angle equally good and
changes nothing. The dihedral that matters is about the group's own internal bond,
which is a different problem and is not solved here.

**A single atom can legitimately become a ring.** Replacing one carbon with a
benzene gives `S-CH2-Ph`: the surviving neighbour is rehybridised to sp3 and
gains the hydrogens that implies. The geometry comes out right — 1.52 Å to the
ring, 1.39 Å inside it — and the result is a real molecule. Do not pre-judge a
selection as too small to act on.

## Verify before publishing

```bash
# The group is there and the rest survived
grep -c "^HETATM\|^ATOM" runs/edited.pdb
grep "BEN" runs/edited.pdb | head
```

The atom count should be `original - selected + heavy + H`. If it equals the
group's size alone, the script was pointed at the wrong `--structure` and the
complex was dropped.

## What this skill does not do

- **No chemistry validation.** The group is placed geometrically; bond orders,
  protonation and valence are not checked, and no bonds are invented between
  the group and its neighbours.
- **No energy check.** Whether the edit is physically reasonable is a question
  for `molecular-dynamics` or `conformational-analysis`, not for this skill.
- **No new molecule design.** This alters what the user pointed at. Generating
  something new is `molecule-generation` or `pocket-molecule-design`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `selection has no atoms` | Viewer is in Residue mode, or nothing selected | Switch to **Atom** mode, select the group to replace |
| `could not resolve the selection` | The structure changed since the selection was made | Re-select on the current structure |
| Group appears in a new chain | Selection label had no chain suffix | Pass `--chain` explicitly |
| Atom count is far off | Wrong `--structure` — the complex was never read | Point at the PDB the selection was made on |
| A bond is drawn that was not asked for | Two-atom alignment left the rotation free | Map every heavy atom, not the first two |
| Hydrogens sit on the attachment | Constant bond length, or atom 0's hydrogen left in | Covalent radii for the bond; delete atom 0's H after the merge |
| Stray atoms where the group goes | Hydrogens orphaned by the deletion | Remove hydrogens with no heavy-atom neighbour |
| Group floats free of the molecule | It bonded to a hydrogen instead of a heavy atom | Filter attachments to heavy atoms |
| RMSD is 0.000 but the group is in the wrong place | `AddConformer` appended to the template's own | `RemoveConformer(0)` before adding the reference |
| Hydrogens clash with the attachment | The group has no plane of its own and its normal was arbitrary | Expected for chains; the bond length is right, the dihedral is not optimised |
| Ring bonds to only one side | The selection spanned two fragments | Expected — one benzene cannot bridge both |
| `sanitize=False` warning | Normal for a PDB read off disk | Ignore; the edit needs only the graph and coordinates |
