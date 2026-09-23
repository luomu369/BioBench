#!/usr/bin/env bash
# BioBench environment bootstrap.
#
# Verifies (and where possible repairs) everything BioBench needs:
#   - conda environments (boltzgen, comfyui)
#   - BoltzGen weights
#   - the viewer's static assets
#
# Read-only by default: reports status and exits non-zero if something is
# missing. Pass --fix to attempt repairs.
#
# Usage:
#   bash bootstrap.sh          # check only
#   bash bootstrap.sh --fix    # check and repair

set -uo pipefail

BIOBENCH="${BIOBENCH:-/home/asus_gx10/biobench}"
CONDA="${CONDA:-/home/asus_gx10/miniconda3}"
MODELS="${MODELS:-/home/asus_gx10/models}"
STATIC="$BIOBENCH/webapp/static"

FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; }
head_() { echo; echo "== $1"; }

# ---------------------------------------------------------------- conda
head_ "conda"
if [[ -x "$CONDA/bin/conda" ]]; then
    ok "conda found: $("$CONDA/bin/conda" --version 2>/dev/null)"
else
    bad "conda not found at $CONDA"
    echo "         install: https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/"
fi

env_exists() { [[ -d "$CONDA/envs/$1" ]]; }

# ---------------------------------------------------------------- boltzgen env
head_ "conda env: boltzgen"
if env_exists boltzgen; then
    PY="$CONDA/envs/boltzgen/bin/python"
    if "$PY" -c "import boltzgen, torch" 2>/dev/null; then
        ok "boltzgen importable, torch $("$PY" -c 'import torch;print(torch.__version__)')"
    else
        bad "boltzgen env exists but imports fail"
        [[ $FIX -eq 1 ]] && {
            echo "         fixing: pip install boltzgen"
            "$PY" -m pip install -q boltzgen && ok "reinstalled boltzgen"
        }
    fi
    if "$PY" -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
        ok "CUDA available: $("$PY" -c 'import torch;print(torch.cuda.get_device_name(0))')"
    else
        bad "CUDA not available in boltzgen env"
    fi
else
    bad "conda env 'boltzgen' missing"
    [[ $FIX -eq 1 ]] && {
        echo "         fixing: creating env (this takes several minutes)"
        "$CONDA/bin/conda" create -n boltzgen python=3.12 -y --solver=classic >/dev/null 2>&1
        "$CONDA/envs/boltzgen/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cu130
        "$CONDA/envs/boltzgen/bin/pip" install -q boltzgen
        env_exists boltzgen && ok "created boltzgen env"
    }
fi

# ---------------------------------------------------------------- weights
head_ "BoltzGen weights"
CKPTS=(boltzgen1_diverse boltzgen1_adherence boltzgen1_structuretrained_small
       boltz2_conf_final boltz2_aff)
WDIR="$MODELS/boltzgen-1"
if [[ -d "$WDIR" ]]; then
    MISSING=()
    for c in "${CKPTS[@]}"; do
        f="$WDIR/$c.ckpt"
        if [[ -f "$f" ]]; then
            sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
            if (( sz < 100000000 )); then
                bad "$c.ckpt is only $((sz/1024)) KB (LFS pointer?)"
                MISSING+=("$c")
            fi
        else
            bad "$c.ckpt missing"
            MISSING+=("$c")
        fi
    done
    if (( ${#MISSING[@]} == 0 )); then
        ok "all 5 checkpoints present ($(du -sh "$WDIR" | cut -f1))"
    elif [[ $FIX -eq 1 ]]; then
        echo "         fixing: downloading ${#MISSING[@]} checkpoint(s) via hf-mirror"
        HF_ENDPOINT=https://hf-mirror.com "$CONDA/envs/boltzgen/bin/python" - "${MISSING[@]}" <<'PYEOF'
import sys
from huggingface_hub import hf_hub_download
for name in sys.argv[1:]:
    print(f"         downloading {name}.ckpt ...")
    hf_hub_download("boltzgen/boltzgen-1", f"{name}.ckpt",
                    local_dir="/home/asus_gx10/models/boltzgen-1")
PYEOF
    fi
else
    bad "weights directory $WDIR missing"
    [[ $FIX -eq 1 ]] && {
        echo "         fixing: downloading all weights (~9.5 GB)"
        mkdir -p "$WDIR"
        HF_ENDPOINT=https://hf-mirror.com "$CONDA/envs/boltzgen/bin/python" - <<'PYEOF'
from huggingface_hub import snapshot_download
snapshot_download("boltzgen/boltzgen-1",
                  local_dir="/home/asus_gx10/models/boltzgen-1",
                  max_workers=8)
PYEOF
    }
fi

# ---------------------------------------------------------------- proteinmpnn env
head_ "conda env: proteinmpnn"
if env_exists proteinmpnn; then
    PY="$CONDA/envs/proteinmpnn/bin/python"
    if "$PY" -c "import proteinmpnn, torch" 2>/dev/null; then
        ok "proteinmpnn importable, torch $("$PY" -c 'import torch;print(torch.__version__)')"
    else
        bad "proteinmpnn env exists but imports fail"
        [[ $FIX -eq 1 ]] && {
            echo "         fixing: pip install proteinmpnn biotite 'setuptools<81'"
            "$PY" -m pip install -q proteinmpnn biotite "setuptools<81" \
                && ok "reinstalled proteinmpnn"
        }
    fi
    "$PY" -c "import pkg_resources" 2>/dev/null \
        && ok "pkg_resources available" \
        || bad "pkg_resources missing (pip install 'setuptools<81')"
    "$PY" -c "import biotite.structure.io" 2>/dev/null \
        && ok "biotite available (mmCIF to PDB)" \
        || bad "biotite missing (pip install biotite)"
else
    warn "conda env 'proteinmpnn' missing (optional — needed for sequence-design)"
    [[ $FIX -eq 1 ]] && {
        echo "         fixing: creating env"
        "$CONDA/bin/conda" create -n proteinmpnn python=3.12 -y --solver=classic >/dev/null 2>&1
        "$CONDA/envs/proteinmpnn/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cu130
        "$CONDA/envs/proteinmpnn/bin/pip" install -q proteinmpnn biotite "setuptools<81"
        env_exists proteinmpnn && ok "created proteinmpnn env"
    }
fi

# ---------------------------------------------------------------- genmol env
head_ "conda env: genmol"
if env_exists genmol; then
    PY="$CONDA/envs/genmol/bin/python"
    if "$PY" -c "import genmol" 2>/dev/null; then
        ok "genmol importable, torch $("$PY" -c 'import torch;print(torch.__version__)')"
    else
        bad "genmol env exists but import fails"
        [[ $FIX -eq 1 ]] && {
            echo "         fixing: pip install -e /tmp/genmol"
            [[ -d /tmp/genmol ]] || git clone --depth 1 https://github.com/NVIDIA-BioNeMo/GenMol.git /tmp/genmol
            (cd /tmp/genmol && "$PY" -m pip install -q -e .) && ok "reinstalled genmol"
        }
    fi
    "$PY" -c "from safe.converter import SAFEConverter" 2>/dev/null \
        && ok "safe.converter importable" \
        || bad "safe package needs the __init__.py fix (see genmol-deploy)"
    if compgen -G "$MODELS/genmol/*/*.ckpt" > /dev/null; then
        ok "weights present ($(du -sh "$MODELS/genmol" | cut -f1))"
    else
        bad "no GenMol checkpoints under $MODELS/genmol"
    fi
else
    warn "conda env 'genmol' missing (optional — needed for molecule-generation)"
    [[ $FIX -eq 1 ]] && {
        echo "         fixing: creating env"
        "$CONDA/bin/conda" create -n genmol python=3.12 -y --solver=classic >/dev/null 2>&1
        "$CONDA/envs/genmol/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cu130
        "$CONDA/envs/genmol/bin/pip" install -q safe-mol openbabel-wheel easydict \
            einops jaxtyping pandas omegaconf hydra-core huggingface_hub
        git clone --depth 1 https://github.com/NVIDIA-BioNeMo/GenMol.git /tmp/genmol
        (cd /tmp/genmol && "$CONDA/envs/genmol/bin/pip" install -q -e .)
        env_exists genmol && ok "created genmol env"
    }
fi

# ---------------------------------------------------------------- foldseek
head_ "foldseek"
FS="$HOME/tools/foldseek/bin/foldseek"
if [[ -x "$FS" ]] || command -v foldseek >/dev/null 2>&1; then
    BIN=$(command -v foldseek 2>/dev/null || echo "$FS")
    VER=$("$BIN" version 2>/dev/null | head -1 | cut -c1-12)
    ok "foldseek $VER ($BIN)"
else
    warn "foldseek not installed (optional — needed for structure-comparison)"
    [[ $FIX -eq 1 ]] && {
        echo "         fixing: downloading arm64 release binary"
        mkdir -p "$HOME/tools"
        (cd "$HOME/tools" \
            && curl -sL -o fs.tar.gz "https://github.com/steineggerlab/foldseek/releases/download/10-941cd33/foldseek-linux-arm64.tar.gz" \
            && tar xzf fs.tar.gz && rm -f fs.tar.gz) \
            && ok "installed foldseek to $HOME/tools/foldseek"
    }
fi

# ---------------------------------------------------------------- viewer assets
head_ "viewer assets"
for f in index.html 3Dmol-min.js; do
    if [[ -f "$STATIC/$f" ]]; then
        ok "$f present"
    else
        bad "$f missing in $STATIC"
    fi
done

# ---------------------------------------------------------------- viewer running
head_ "viewer process"
if curl -s -o /dev/null --max-time 3 http://localhost:8282/; then
    ok "viewer responding on :8282"
else
    warn "viewer not running on :8282"
    echo "         start: bash $BIOBENCH/webapp/run_viewer.sh --port 8282"
fi

# ---------------------------------------------------------------- summary
echo
echo "== summary: $PASS passed, $FAIL failed"
(( FAIL == 0 )) || exit 1
