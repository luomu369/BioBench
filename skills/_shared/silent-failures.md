# Silent failures this codebase has already hit

Each of these produced output that looked plausible and was wrong. Nothing
errored. The file wrote, the viewer drew it, the number printed. They are
collected here because they are not specific to one skill — the same root
cause shows up wherever a structure is read, written, or handed to a tool.

A skill's own SKILL.md carries the traps specific to its chemistry. This file
carries the ones about the formats and the tools, which every skill shares.

## Fixed-column formats have no error checking

PDB and PDBQT are fixed-column, and every column boundary is implicit in an
f-string or a slice. Nothing validates them. A format string with one space
too many shifts the residue name into the atom-name field; a residue number
above 9999 overflows its four columns and pushes the chain ID into the residue
name; a serial that restarts at 1 in a file appended to another collides with
the first file's atoms. None of these raise — the file writes and a parser
reads a different molecule from the one that was meant.

Four of these happened in one session, in three scripts, because each had
written its own copy of the column layout.

**Use `_shared/pdb_format.py`.** It owns the layout for PDB: `atom_line`
refuses a value that would not fit rather than shifting the columns after it,
`iter_atoms` returns fields by name so no caller slices, and
`check_serials_unique` catches a duplicate serial or a CONECT naming an atom
that is not there. Run `python3 skills/_shared/pdb_format.py` after editing it.

**PDBQT is not covered yet.** Its element column moves: AutoDock's atom type
is one or two characters (`C`, `Cl`, `NA`, `OA`), and a two-character type
pushes the element one column right, so a 79-column line has `Cl` at
`[77:79]` and not `[76:78]`. Reading the fixed column returns `" C"` for a
chlorine and the atom comes out a carbon — invisible in the file, obvious on
screen where it renders the wrong colour. Read the element from the end of the
line, not from a column.

## `proximityBonding` bonds on distance alone

`Chem.MolFromPDBFile(..., proximityBonding=True)` is the only way to read a PDB
that has no CONECT records, and it bonds anything close enough. That includes
things that are not bonds:

- **Hydrogens on the selected group's own atoms** look like surviving
  neighbours. Bonding a new group to one leaves the real attachment dangling.
- **A protein side chain 1.3 Å from a ligand's amine** reads as a bond to it,
  and the new group is then bonded to the protein as well as to the ligand —
  drawing a line between two molecules that was never there.

Filter attachments to heavy atoms *and* to the same residue as the selection.

## meeko drops residues it cannot template, silently

`mk_prepare_receptor.py -a` (allow_bad_res) tells meeko to drop any residue it
cannot build a chemical template for, and it drops them without a word. A
crystal structure with disordered side chains reaches Vina with holes in the
binding pocket — and a ligand that fits a hole scores better than one that
does not.

One run scored **-10.7 kcal/mol** against a receptor missing a pocket
phenylalanine, and **-6.0** once that residue was restored. The score and the
picture disagreed because the complex was assembled from the input PDB while
Vina had docked against the PDBQT.

Three things follow:

1. **Parse the dropped list out of meeko's report** and carry it in the
   summary. `dock.py` writes `receptor_residues_dropped`.
2. **Build the complex from the PDBQT**, converted back — the receptor Vina
   actually used, hydrogens and all — not from the input PDB.
3. **Check the receptor for completeness before docking.** A residue missing
   its backbone N/C/O cannot be templated at all; one missing side-chain
   termini may be padded. `dock_flex.py` writes only side chains, so its
   output needs the backbone restored from the source structure before it can
   be docked against.

## A protein written as HETATM is not a protein

meeko's receptor preparer templates `ATOM` residues as protein and treats
`HETATM` ones as ligands. A protein written entirely as HETATM reaches the
docking step with whole side chains missing — it cannot template a PHE as a
ligand and drops it.

Write standard amino acids as `ATOM` and everything else as `HETATM`.
`_shared/pdb_format.py` takes a `record` argument; `replace_with_group.py`
picks it from `STANDARD_AA`.

## Vina's score is not a clash check

Vina's scoring function softens the van der Waals term, so a pose with real
overlap can still score well. "The affinity improved" and "the structure is
sound" are separate claims.

Check the clash directly — same-element heavy atoms closer than ~2.2 Å — and
say so when reporting a score. A score computed against a receptor with holes
in it is not comparable to one computed against a whole receptor, and neither
is comparable across a rigid and a flexible run.

## The status bar is a file, not a live view

`status.json` is written to disk and polled every 2 seconds. It outlives the
viewer, the shell and the reboot, so the pill at the bottom of the screen
shows the last thing *anyone* published — a smoke test, a manual
`publish.py` call, a run that finished hours ago — and it looks exactly like
a current one.

`publish.py` generates no status text of its own. `--message` is free text
stored verbatim, so a pill reading "全部通过" is a claim made by whoever
passed that flag, not a verdict any tool reached. One session left a
pipeline smoke test's message on the pill, and it was later read as the
state of the machine.

Check `active.updated` before reading the pill as progress, and say so when
it belongs to an earlier session. To clear it, publish something — there is
no reset flag, and the next `--status` call overwrites `active`. When you
publish to verify the pipeline works, word the message as what it was
("发布链路验证"), not as a conclusion.

## The viewer's atom serial is not the PDB serial

3Dmol's `atom.serial` is its own per-model counter, starting at 1 for every
model it builds. On a structure whose ligand is numbered 4075-4103 it reports
8, and a skill told "serial 8" edits the receptor's eighth atom instead of the
oxygen that was clicked. It agrees with the PDB serial whenever a structure's
atoms are numbered from 1, which is why the mistake survived as long as it
did.

Take the serial from `atom.pdbline`, which is the original PDB record.
