#!/usr/bin/env bash
# setup.sh - clone the Slicer sources, extension index and discourse archive for local searches
#
# Three modes:
#   full        (~15 GB)  — everything local: source, deps, all extensions, discourse archive
#   lightweight (~1 GB)   — Slicer source + ExtensionsIndex metadata only; extensions on-demand
#   web         (minimal) — nothing cloned; agent uses GitHub API + Discourse API
#
# Usage:
#   ./setup.sh                     # interactive mode-selection (first run) or skip if recent
#   ./setup.sh --mode full         # non-interactive
#   ./setup.sh --mode lightweight
#   ./setup.sh --mode web
#   ./setup.sh --force             # re-run ignoring 24h cooldown, keep previous mode
#   ./setup.sh --force --mode full # re-run with explicit mode

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP_FILE="$SCRIPT_DIR/.setup-stamp.json"
MAX_AGE_SECONDS=86400  # 24 hours

# ─── Parse arguments ──────────────────────────────────────────
FORCE=false
REQUESTED_MODE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        --mode)  REQUESTED_MODE="$2"; shift 2 ;;
        *)       echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ─── Read previous stamp ─────────────────────────────────────
prev_mode=""
if [ -f "$STAMP_FILE" ]; then
    prev_mode=$(perl -ne 'print $1 if /"mode"\s*:\s*"([^"]+)"/' "$STAMP_FILE" 2>/dev/null || true)
fi

# Skip setup if the stamp file exists and is less than MAX_AGE_SECONDS old.
if [ "$FORCE" = false ] && [ -f "$STAMP_FILE" ]; then
    stamp_epoch=$(perl -ne 'print $1 if /"epoch"\s*:\s*(\d+)/' "$STAMP_FILE" 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    age=$(( now_epoch - stamp_epoch ))
    if [ "$age" -lt "$MAX_AGE_SECONDS" ]; then
        echo "Setup last ran $(( age / 3600 ))h$(( (age % 3600) / 60 ))m ago (< 24h), mode=${prev_mode:-unknown}. Skipping. Use --force to override."
        exit 0
    fi
fi

# ─── Disk space check ────────────────────────────────────────
get_available_gb() {
    if df -Pk "$SCRIPT_DIR" >/dev/null 2>&1; then
        df -Pk "$SCRIPT_DIR" | awk 'NR==2 {printf "%.1f", $4/1048576}'
    else
        echo "unknown"
    fi
}

AVAIL_GB=$(get_available_gb)

# ─── Determine mode ──────────────────────────────────────────
MODE=""
if [ -n "$REQUESTED_MODE" ]; then
    case "$REQUESTED_MODE" in
        full|lightweight|web) MODE="$REQUESTED_MODE" ;;
        *) echo "Invalid mode: $REQUESTED_MODE (must be full, lightweight, or web)"; exit 1 ;;
    esac
elif [ -n "$prev_mode" ]; then
    # Re-use previous mode on subsequent runs
    MODE="$prev_mode"
    echo "Re-using previous mode: $MODE"
else
    # First run — interactive prompt
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║              Slicer Skill — Setup Mode Selection            ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║                                                            ║"
    echo "║  1) full        (~15 GB, ~20 min)                          ║"
    echo "║     Everything local: source, dependencies, all 200+       ║"
    echo "║     extensions, discourse archive. Fastest grep/find/git.  ║"
    echo "║                                                            ║"
    echo "║  2) lightweight (~1 GB, ~2 min)                            ║"
    echo "║     Slicer source + ExtensionsIndex metadata only.         ║"
    echo "║     Extensions cloned on-demand. Discourse via web API.    ║"
    echo "║                                                            ║"
    echo "║  3) web         (minimal disk, instant)                    ║"
    echo "║     Nothing cloned. All access via GitHub API and          ║"
    echo "║     Discourse API. Slower per-query but zero setup.        ║"
    echo "║                                                            ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    if [ "$AVAIL_GB" != "unknown" ]; then
        echo "  Available disk space: ${AVAIL_GB} GB"
        # Warn if full mode would be tight
        avail_int=${AVAIL_GB%.*}
        if [ "$avail_int" -lt 20 ] 2>/dev/null; then
            echo "  ⚠  Less than 20 GB free — 'full' mode may strain your disk."
        fi
        if [ "$avail_int" -lt 3 ] 2>/dev/null; then
            echo "  ⚠  Less than 3 GB free — consider 'web' mode."
        fi
    fi
    echo ""

    if [ -t 0 ]; then
        # Interactive terminal
        printf "  Select mode [1/2/3] (default: 2 lightweight): "
        read -r choice
        case "${choice:-2}" in
            1|full)        MODE="full" ;;
            2|lightweight) MODE="lightweight" ;;
            3|web)         MODE="web" ;;
            *) echo "Invalid choice, defaulting to lightweight."; MODE="lightweight" ;;
        esac
    else
        # Non-interactive (e.g. agent running setup) — default to lightweight
        echo "  Non-interactive environment detected. Defaulting to lightweight."
        echo "  Use --mode full|lightweight|web to override."
        MODE="lightweight"
    fi
fi

echo ""
echo "Setup mode: $MODE"
echo ""

# ─── Web mode: no cloning needed ─────────────────────────────
if [ "$MODE" = "web" ]; then
    echo "Web-only mode — no repositories will be cloned."
    echo "The agent will use GitHub API and Discourse API for all searches."
    printf '{"epoch": %d, "iso": "%s", "mode": "%s"}\n' \
        "$(date +%s)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" > "$STAMP_FILE"
    echo ""
    echo "Setup complete (web mode)."
    exit 0
fi

# ─── Shared helpers ───────────────────────────────────────────

# locations may be overridden using environment variables
: "${SLICER_SRC_DIR:=slicer-source}"
: "${SLICER_EXT_DIR:=slicer-extensions}"
: "${SLICER_DISCOURSE_DIR:=slicer-discourse}"
: "${SLICER_DEP_DIR:=slicer-dependencies}"

# optional filter: space-separated list of extension names to fetch.  Leave empty to
# clone everything.
EXTENSION_FILTER=""

clone_or_pull() {
    local url="$1"
    local dest="$2"
    if [ -d "$dest/.git" ]; then
        update_repo "$dest"
    else
        echo "Cloning $url -> $dest"
        git clone --depth 1 "$url" "$dest"
    fi
}

# Update an existing git repo: if HEAD is detached, fetch tags/branches and skip pull
update_repo() {
    local dest="$1"
    if [ ! -d "$dest/.git" ]; then
        echo "Not a git repo: $dest"
        return 0
    fi
    echo "Updating $dest..."
    branch=$(git -C "$dest" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")
    if [ "$branch" = "HEAD" ]; then
        echo "Detached HEAD in $dest — fetching and skipping pull."
        git -C "$dest" fetch --all --tags --prune 2>/dev/null || true
    else
        git -C "$dest" pull --ff-only 2>/dev/null || true
    fi
}

# ─── 1. main Slicer source (both full and lightweight) ────────
clone_or_pull "https://github.com/Slicer/Slicer.git" "$SLICER_SRC_DIR"

# ─── 1b. SuperBuild dependencies (full mode only) ────────────
clone_superbuild_deps() {
    local base="$SLICER_SRC_DIR"
    local outdir="$SLICER_DEP_DIR"
    mkdir -p "$outdir"
    seen_file=$(mktemp)
    cmake_vars_file=$(mktemp)
    # ensure temp files are removed when function exits
    cleanup_clone_superbuild_deps() { rm -f "$seen_file" "$cmake_vars_file"; }
    trap cleanup_clone_superbuild_deps RETURN

    normalize_repo() {
        local repo="$1"
        # strip surrounding quotes
        repo="${repo%\"}"
        repo="${repo#\"}"
        # replace ${EP_GIT_PROTOCOL}:// and $EP_GIT_PROTOCOL:// with https://
        repo="${repo//\$\{EP_GIT_PROTOCOL\}:\/\//https://}"
        repo="${repo//\$EP_GIT_PROTOCOL:\/\//https://}"
        # replace ${EP_GIT_PROTOCOL} or $EP_GIT_PROTOCOL with https
        repo="${repo//\$\{EP_GIT_PROTOCOL\}/https}"
        repo="${repo//\$EP_GIT_PROTOCOL/https}"
        echo "$repo"
    }

    files_to_scan=()
    [ -f "$base/CMakeLists.txt" ] && files_to_scan+=("$base/CMakeLists.txt")
    [ -f "$base/SuperBuild.cmake" ] && files_to_scan+=("$base/SuperBuild.cmake")
    if [ -d "$base/Utilities/Templates/Extensions/SuperBuild/SuperBuild" ]; then
        while IFS= read -r -d $'\0' f; do files_to_scan+=("$f"); done < <(find "$base/Utilities/Templates/Extensions/SuperBuild/SuperBuild" -type f -name "*.cmake" -print0)
    fi
    if [ -d "$base/SuperBuild" ]; then
        while IFS= read -r -d $'\0' f; do files_to_scan+=("$f"); done < <(find "$base/SuperBuild" -type f -name "*.cmake" -print0)
    fi

    # Parse SuperBuild files for ExternalProject_SetIfNotDefined and simple set(...) vars
        for sf in "${files_to_scan[@]}"; do
            perl -0777 -ne '
                my %set=();
                while(/set\(\s*([A-Za-z0-9_]+)\s+(?:"([^"]+)"|([^\)\s#]+))/g){ $set{$1}= defined $2 ? $2 : $3 }
                for my $k (keys %set) { print "$k|$set{$k}\n" }
                while(/ExternalProject_SetIfNotDefined\(\s*([^\s\)]+)\s*(?:"([^"]+)"|([^\)\s#]+))/g){
                $var=$1; $val = defined $2 ? $2 : $3;
                $var2 = $var;
                $var2 =~ s/\$\{([A-Za-z0-9_]+)\}/ (defined $set{$1} ? $set{$1} : "\$\{$1\}") /ge;
                print "$var2|$val\n";
                }
            ' "$sf" | while IFS='|' read -r v val; do
                if [ -n "$v" ] && [ -n "$val" ]; then
                    printf '%s|%s\n' "$v" "$val" >> "$cmake_vars_file"
                fi
            done
        done

        get_cmake_var() {
            awk -F'|' -v k="$1" '$1==k {val=$2} END{ if(val) print val }' "$cmake_vars_file" || true
        }

        for file in "${files_to_scan[@]}"; do
                perl -0777 -ne '
                    while(/GIT_REPOSITORY\s+(?:"([^"]+)"|([^ \t#\n]+))/g) {
                        $repo = defined $1 ? $1 : $2;
                        $rest = substr($_, pos());
                        $tag = "";
                        if($rest =~ /GIT_TAG\s+(?:"([^"]+)"|([^ \t#\n]+))/) { $tag = defined $1 ? $1 : $2 }
                        print "$repo|$tag\n";
                    }
                ' "$file" | while IFS='|' read -r repo tag; do
                    repo=$(normalize_repo "$repo")
                    # resolve variable-based repo names from parsed SuperBuild vars
                    if [[ "$repo" == *'${'* ]]; then
                        # replace ${VAR} with value when known
                        tmp="$repo"
                        while [[ "$tmp" =~ \$\{([A-Za-z0-9_]+)\} ]]; do
                            key=${BASH_REMATCH[1]}
                            val=$(get_cmake_var "$key")
                            if [ -z "$val" ]; then break; fi
                            tmp=${tmp//\$\{$key\}/$val}
                        done
                        repo="$tmp"
                    fi
                    if [ -z "$repo" ] || [[ "$repo" == *'${'* ]]; then
                continue
            fi
                    # skip tokens that are not URL-like (e.g. single words like 'and')
                    if [[ "$repo" != *"/"* ]]; then
                        continue
                    fi
            name=$(basename "$repo" .git)
            if [ -z "$name" ]; then
                continue
            fi
                    if grep -Fq "$repo" "$seen_file" 2>/dev/null; then
                continue
            fi
            printf '%s\n' "$repo" >> "$seen_file"
            dest="$outdir/$name"
            if [ -d "$dest/.git" ]; then
                update_repo "$dest" || true
            else
                echo "Cloning dependency $repo -> $dest"
                    # resolve tag variables from parsed SuperBuild vars
                    if [[ "$tag" == *'${'* ]]; then
                        tmp_tag="$tag"
                        while [[ "$tmp_tag" =~ \$\{([A-Za-z0-9_]+)\} ]]; do
                            k=${BASH_REMATCH[1]}; v=$(get_cmake_var "$k"); if [ -z "$v" ]; then break; fi
                            tmp_tag=${tmp_tag//\$\{$k\}/$v}
                        done
                        tag="$tmp_tag"
                    fi
                    if [ -n "$tag" ] && [[ "$tag" != *'${'* ]] && [[ "$tag" != *\$* ]]; then
                    # try shallow clone by tag/branch first; fall back to full clone
                    if ! git clone --depth 1 --branch "$tag" "$repo" "$dest" 2>/dev/null; then
                        git clone "$repo" "$dest" || true
                        # ensure we have remote refs/tags and retry checkout of the requested tag/sha
                        if [ -d "$dest/.git" ]; then
                            git -C "$dest" fetch --all --tags --prune 2>/dev/null || true
                            git -C "$dest" -c advice.detachedHead=false checkout "$tag" 2>/dev/null || true
                        fi
                    fi
                else
                    git clone --depth 1 "$repo" "$dest" || true
                    if [ -n "$tag" ] && [ -d "$dest/.git" ] && [[ "$tag" != *'${'* ]] && [[ "$tag" != *\$* ]]; then
                        git -C "$dest" fetch --all --tags --prune 2>/dev/null || true
                        git -C "$dest" -c advice.detachedHead=false checkout "$tag" 2>/dev/null || true
                    fi
                fi
            fi
        done
    done

    # Special-case: resolve VTK _git_tag based on default VTK version from top-level CMakeLists
    if [ -f "$base/CMakeLists.txt" ] && [ -f "$base/SuperBuild/External_VTK.cmake" ]; then
        default_major=$(perl -0777 -ne 'print $1 if /set\(_default_vtk_major_version\s+"?([0-9]+)/s' "$base/CMakeLists.txt" 2>/dev/null || true)
        default_minor=$(perl -0777 -ne 'print $1 if /set\(_default_vtk_minor_version\s+"?([0-9]+)/s' "$base/CMakeLists.txt" 2>/dev/null || true)
        if [ -n "$default_major" ] && [ -n "$default_minor" ]; then
            want="${default_major}.${default_minor}"
            vtk_tag=$(perl -0777 -ne '
              $want = shift; if(/if\([^)]*STREQUAL\s+"\Q$want\E"\)(.*?)((?:elseif|else|endif)|$)/s){ $blk=$1; if($blk=~ /set\(\s*_git_tag\s+"([^"]+)"/){ print $1 } }
            ' "$want" "$base/SuperBuild/External_VTK.cmake" 2>/dev/null || true)
            if [ -n "$vtk_tag" ]; then
                printf '%s|%s\n' "_git_tag" "$vtk_tag" >> "$cmake_vars_file"
            fi
        fi
    fi
}

if [ "$MODE" = "full" ]; then
    clone_superbuild_deps
fi

# ─── 2. Extensions index ─────────────────────────────────────
# Both full and lightweight get the index (it's tiny — just JSON metadata)
clone_or_pull "https://github.com/Slicer/ExtensionsIndex.git" "$SLICER_EXT_DIR"

# Clone all extension repos (full mode only)
if [ "$MODE" = "full" ] && [ -d "$SLICER_EXT_DIR" ]; then
    echo "Processing extensions index..."
    tmpfile=$(mktemp)
    trap 'rm -f "$tmpfile"' EXIT
    # collect repo|name pairs as NUL-separated entries
    find "$SLICER_EXT_DIR" -name "*.json" -print0 | while IFS= read -r -d '' file; do
        repo=$(perl -ne 'print $1 if /"scm_url"\s*:\s*"([^\"]+)"/' "$file" || true)
        if [ -n "$repo" ]; then
            name=$(basename "$repo" .git)
            if [ -n "$EXTENSION_FILTER" ] && ! [[ " $EXTENSION_FILTER " =~ " $name " ]]; then
                continue
            fi
            printf '%s|%s\0' "$repo" "$name" >> "$tmpfile"
        fi
    done

    # determine parallelism: use `nproc * 4` (for better network utilization),
    # ensure at least 6 jobs and cap at 16
    nproc_val=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
    jobs=$(( nproc_val * 4 ))
    if [ "$jobs" -lt 6 ]; then jobs=6; fi
    if [ "$jobs" -gt 16 ]; then jobs=16; fi

    # worker: update or clone each repo in parallel
    if [ -s "$tmpfile" ]; then
        cat "$tmpfile" | xargs -0 -n1 -P "$jobs" bash -c '
            pair="$1"; url=${pair%%|*}; name=${pair#*|}; dest="'"$SLICER_EXT_DIR"'"/"$name";
            if [ -d "$dest/.git" ]; then
                branch=$(git -C "$dest" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD");
                if [ "$branch" = "HEAD" ]; then
                    echo "Detached HEAD in $dest — fetching and skipping pull.";
                    git -C "$dest" fetch --all --tags --prune 2>/dev/null || true;
                else
                    git -C "$dest" pull --ff-only 2>/dev/null || true;
                fi
            else
                echo "Cloning $url -> $dest"; git clone --depth 1 "$url" "$dest" || true;
            fi
        ' sh
    fi
    rm -f "$tmpfile"
fi

# ─── 3. Discourse archive (full mode only) ───────────────────
if [ "$MODE" = "full" ]; then
    clone_or_pull "https://github.com/pieper/slicer-discourse-archive.git" "$SLICER_DISCOURSE_DIR"
fi

# ─── 4. Coding conversations (optional, full and lightweight) ─
: "${CODING_CHATS_DIR:=CodingChats-conversations}"
if [ -z "${CODING_CHATS_REPO:-}" ] && command -v gh >/dev/null 2>&1; then
    gh_user=$(gh api user --jq '.login' 2>/dev/null || true)
    if [ -n "$gh_user" ]; then
        candidate="https://github.com/${gh_user}/CodingChats-conversations.git"
        if gh repo view "${gh_user}/CodingChats-conversations" >/dev/null 2>&1; then
            CODING_CHATS_REPO="$candidate"
            echo "Found coding conversations repo: $candidate"
        fi
    fi
fi
if [ -n "${CODING_CHATS_REPO:-}" ]; then
    clone_or_pull "$CODING_CHATS_REPO" "$CODING_CHATS_DIR"
fi

# ─── Write stamp ─────────────────────────────────────────────
printf '{"epoch": %d, "iso": "%s", "mode": "%s"}\n' \
    "$(date +%s)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" > "$STAMP_FILE"

echo ""
echo "Setup complete (mode: $MODE)."
