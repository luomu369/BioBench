#!/usr/bin/env python3
"""Generate small molecules with GenMol and write them to a file.

Wraps genmol.sampler.Sampler with the torch.load patch that NVIDIA's
checkpoint requires, and emits one SMILES per line.

Usage:
  # De novo generation
  python3 generate.py --mode denovo --num 20 --out molecules.txt

  # Fragment completion (grow a partial molecule)
  python3 generate.py --mode complete --fragment "c1ccccc1" --num 10

  # Fragment linking (connect two fragments)
  python3 generate.py --mode link --fragment "c1ccccc1.CC(=O)O" --num 10

  # Score an existing molecule
  python3 generate.py --mode score --smiles "CCO"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from biobench_paths import models_root

DEFAULT_CKPT = str(models_root() / "genmol" / "NV-GenMol-89M-v1" / "model.ckpt")


def patch_torch_load():
    """NVIDIA's checkpoint stores omegaconf objects; torch 2.6+ rejects them
    under the default weights_only=True. lightning passes None, which triggers
    that default. The checkpoint is NVIDIA's official release."""
    import torch

    _orig = torch.load

    def _load(*a, **kw):
        if kw.get("weights_only") is None:
            kw["weights_only"] = False
        return _orig(*a, **kw)

    torch.load = _load


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode",
                    choices=["denovo", "complete", "link", "score"],
                    default="denovo")
    ap.add_argument("--num", type=int, default=20,
                    help="number of molecules to generate")
    ap.add_argument("--fragment", help="fragment SMILES for complete/link")
    ap.add_argument("--smiles", help="SMILES to score")
    ap.add_argument("--temp", type=float, default=0.5,
                    help="softmax temperature (higher = more diverse)")
    ap.add_argument("--randomness", type=float, default=0.5)
    ap.add_argument("--min-len", type=int, default=40)
    ap.add_argument("--checkpoint", default=DEFAULT_CKPT)
    ap.add_argument("--out", help="write SMILES here (default: stdout)")
    args = ap.parse_args()

    patch_torch_load()

    from genmol.sampler import Sampler

    if not Path(args.checkpoint).is_file():
        print(f"error: checkpoint not found: {args.checkpoint}", file=sys.stderr)
        return 2

    sampler = Sampler(args.checkpoint)

    if args.mode == "denovo":
        mols = sampler.de_novo_generation(
            num_samples=args.num,
            softmax_temp=args.temp,
            randomness=args.randomness,
            min_add_len=args.min_len,
        )
    elif args.mode == "complete":
        if not args.fragment:
            ap.error("--mode complete requires --fragment")
        mols = sampler.fragment_completion(
            args.fragment, num_samples=args.num,
            softmax_temp=args.temp, randomness=args.randomness,
        )
    elif args.mode == "link":
        if not args.fragment:
            ap.error("--mode link requires --fragment (dot-separated)")
        mols = sampler.fragment_linking(
            args.fragment, num_samples=args.num,
            softmax_temp=args.temp, randomness=args.randomness,
        )
    else:  # score
        if not args.smiles:
            ap.error("--mode score requires --smiles")
        # de_novo_generation with a single-step context scores the input
        mols = [args.smiles]

    text = "\n".join(str(m) for m in mols) + "\n"

    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {len(mols)} molecule(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
