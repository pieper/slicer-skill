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
REQUESTED_INDEXES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --force)   FORCE=true; shift ;;
        --mode)    REQUESTED_MODE="$2"; shift 2 ;;
        --indexes) REQUESTED_INDEXES="$2"; shift 2 ;;
        --help|-h)
            cat <<HELP
Usage: ./setup.sh [--force] [--mode MODE] [--indexes LEVEL]

  --mode    full | lightweight | web    (which clones to fetch)
  --indexes none | bm25  | hybrid       (which search indexes to build)
  --force   re-run ignoring 24h cooldown

First run prompts interactively for both mode and index level.
Subsequent runs reuse the previous choices.
HELP
            exit 0 ;;
        *) echo "Unknown argument: $1 (try --help)"; exit 1 ;;
    esac
done

# ─── Read previous stamp ─────────────────────────────────────
prev_mode=""
prev_indexes=""
if [ -f "$STAMP_FILE" ]; then
    prev_mode=$(perl -ne 'print $1 if /"mode"\s*:\s*"([^"]+)"/' "$STAMP_FILE" 2>/dev/null || true)
    prev_indexes=$(perl -ne 'print $1 if /"indexes"\s*:\s*"([^"]+)"/' "$STAMP_FILE" 2>/dev/null || true)
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

# ─── Determine search-index level ────────────────────────────
# Three levels:
#   none    no ranked search; agent uses grep/find/web
#   bm25    lexical search only (~15 s setup, ~65 MB)
#   hybrid  bm25 + dense embeddings (~15 s + ~20 min in BACKGROUND, ~300 MB)
# Web mode forces 'none' since there are no local clones to index.
INDEXES=""
if [ -n "$REQUESTED_INDEXES" ]; then
    case "$REQUESTED_INDEXES" in
        none|bm25|hybrid) INDEXES="$REQUESTED_INDEXES" ;;
        *) echo "Invalid --indexes: $REQUESTED_INDEXES (must be none, bm25, or hybrid)"; exit 1 ;;
    esac
elif [ "$MODE" = "web" ]; then
    INDEXES="none"
elif [ -n "$prev_indexes" ]; then
    INDEXES="$prev_indexes"
    echo "Re-using previous index level: $INDEXES"
elif [ -t 0 ]; then
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║              Search Index Selection                          ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║                                                              ║"
    echo "║  1) none     no ranked search; agent uses grep/find/web      ║"
    echo "║              cost: 0 setup, 0 disk                           ║"
    echo "║                                                              ║"
    echo "║  2) bm25     lexical only — exact keyword/identifier matches ║"
    echo "║              cost: ~15 s, ~65 MB                             ║"
    echo "║                                                              ║"
    echo "║  3) hybrid   bm25 + dense embeddings (semantic + paraphrase) ║"
    echo "║              cost: ~15 s up front, then ~20 min in the       ║"
    echo "║                    BACKGROUND (you can keep working);        ║"
    echo "║                    ~300 MB.  Lexical search is ready         ║"
    echo "║                    immediately; vector search becomes        ║"
    echo "║                    available when the background job ends.   ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    printf "  Select index level [1/2/3]: "
    read -r choice
    case "${choice}" in
        1|none)   INDEXES="none" ;;
        2|bm25)   INDEXES="bm25" ;;
        3|hybrid) INDEXES="hybrid" ;;
        *) echo "Invalid choice, defaulting to bm25."; INDEXES="bm25" ;;
    esac
else
    echo "  Non-interactive: defaulting to indexes=bm25."
    echo "  Use --indexes none|bm25|hybrid to override."
    INDEXES="bm25"
fi
echo "Index level: $INDEXES"
echo ""

# Tiny helper: write the stamp with both fields
write_stamp() {
    printf '{"epoch": %d, "iso": "%s", "mode": "%s", "indexes": "%s"}\n' \
        "$(date +%s)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "$INDEXES" > "$STAMP_FILE"
}

# ─── Web mode: no cloning needed ─────────────────────────────
if [ "$MODE" = "web" ]; then
    echo "Web-only mode — no repositories will be cloned."
    echo "The agent will use GitHub API and Discourse API for all searches."
    write_stamp
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
: "${SLICER_PROJECTWEEK_DIR:=slicer-projectweek}"

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

# ─── 1b. NA-MIC Project Week repository — NEW FEATURE! ───────
# Provides additional examples and educational materials for Slicer programming
: "${SLICER_PROJECTWEEK_DIR:=slicer-projectweek}"
clone_or_pull "https://github.com/NA-MIC/ProjectWeek.git" "$SLICER_PROJECTWEEK_DIR"

# ─── 2. SuperBuild dependencies (full mode only) ────────────
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

# ─── 3. Coding conversations (optional, full and lightweight) ─
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

# ─── 4. Search indexes + MCP server ──────────────────────────
# Builds a search index over the cloned corpora and registers a stdio MCP
# server (slicer-skill-search) so an agent can do ranked retrieval instead
# of raw grep.  All paths are resolved automatically; users do not need to
# edit anything by hand.
#
# Argument: level ∈ { bm25, hybrid }
#   bm25    — BM25 only.  Fast (~15 s, ~65 MB).  Built synchronously.
#   hybrid  — BM25 (synchronous, ~15 s) AND dense embeddings (BACKGROUND,
#             ~20 min, ~300 MB).  Lexical search is usable as soon as
#             setup.sh exits; vector search becomes available when the
#             background build completes.  Progress: tail .vector-build.log
#             or call the MCP `index_status` tool.
setup_search_indexes() {
    local level="$1"
    local venv_dir="$SCRIPT_DIR/.venv"
    local req_file="$SCRIPT_DIR/scripts/requirements.txt"
    local bm25_builder="$SCRIPT_DIR/scripts/build_bm25.py"
    local vec_builder="$SCRIPT_DIR/scripts/build_vector.py"
    local mcp_script="$SCRIPT_DIR/slicer-skill-search-mcp.py"
    local mcp_config="$SCRIPT_DIR/.mcp.json"
    local vec_log="$SCRIPT_DIR/.vector-build.log"
    local vec_pid="$SCRIPT_DIR/.vector-build.pid"

    if ! command -v python3 >/dev/null 2>&1; then
        echo "  python3 not found — skipping search setup."
        echo "  Install Python 3.10+ to enable ranked retrieval."
        return 0
    fi

    if [ ! -f "$req_file" ] || [ ! -f "$bm25_builder" ] || [ ! -f "$mcp_script" ]; then
        echo "  Search setup files missing — skipping."
        return 0
    fi

    echo ""
    echo "Setting up search indexes (level: $level)..."

    # Create venv if missing
    if [ ! -x "$venv_dir/bin/python" ]; then
        echo "  Creating venv at $venv_dir"
        python3 -m venv "$venv_dir" || {
            echo "  Failed to create venv — skipping search setup."
            return 0
        }
    fi

    # Install/upgrade dependencies (idempotent — quiet if already current)
    "$venv_dir/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
    if ! "$venv_dir/bin/pip" install --quiet -r "$req_file"; then
        echo "  pip install failed — skipping search setup."
        return 0
    fi

    if [ ! -d "$SCRIPT_DIR/$SLICER_SRC_DIR" ] && [ ! -d "$SCRIPT_DIR/$SLICER_DISCOURSE_DIR" ]; then
        echo "  No local source/discourse clones found — index build skipped."
        return 0
    fi

    # Extra Slicer-specific dependency corpora — only indexed in full mode,
    # since lightweight skips slicer-dependencies/. Kept tight: CTK, vtkAddon,
    # SlicerExecutionModel. VTK/ITK/DCMTK are intentionally excluded — they're
    # huge and their generic API would swamp Slicer-specific results.
    local -a extra_args=()
    if [ "$MODE" = "full" ]; then
        for dep in CTK vtkAddon SlicerExecutionModel; do
            dep_path="$SCRIPT_DIR/$SLICER_DEP_DIR/$dep"
            if [ -d "$dep_path" ]; then
                # Lowercase index name so the MCP tool is search_ctk, etc.
                lname=$(echo "$dep" | tr '[:upper:]' '[:lower:]')
                extra_args+=(--extra "$lname=$dep_path")
            fi
        done
    fi

    # 4a) BM25 (lexical) — fast, runs in seconds, always built
    "$venv_dir/bin/python" "$bm25_builder" \
        --source-dir "$SCRIPT_DIR/$SLICER_SRC_DIR" \
        --discourse-dir "$SCRIPT_DIR/$SLICER_DISCOURSE_DIR" \
        --out "$SCRIPT_DIR/.bm25-index" \
        "${extra_args[@]}" || {
        echo "  BM25 build failed — lexical search will be unavailable."
    }

    # 4b) Vector (dense) — only if user asked for hybrid.  Built in the
    #     BACKGROUND so the user/agent can keep working.  Old PID file
    #     cleared on entry; new PID written for index_status to discover.
    if [ "$level" = "hybrid" ] && [ -f "$vec_builder" ]; then
        # If a previous vector build is still running, don't start another
        if [ -f "$vec_pid" ]; then
            local old_pid
            old_pid=$(cat "$vec_pid" 2>/dev/null || true)
            if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
                echo "  Vector build already running (PID $old_pid).  Skipping launch."
                echo "    Tail progress: tail -f .vector-build.log"
                return 0
            fi
        fi
        echo ""
        echo "  Launching vector index build in BACKGROUND..."
        # nohup + & + disown: child survives after setup.sh exits, log captured
        nohup "$venv_dir/bin/python" -u "$vec_builder" \
            --source-dir "$SCRIPT_DIR/$SLICER_SRC_DIR" \
            --discourse-dir "$SCRIPT_DIR/$SLICER_DISCOURSE_DIR" \
            --out "$SCRIPT_DIR/.vector-index" \
            "${extra_args[@]}" \
            > "$vec_log" 2>&1 &
        local build_pid=$!
        echo "$build_pid" > "$vec_pid"
        disown "$build_pid" 2>/dev/null || true
        echo "    PID:    $build_pid"
        echo "    Log:    tail -f .vector-build.log"
        echo "    ETA:    ~20 min on a recent Mac/Linux"
        echo "    Status: call the MCP 'index_status' tool — it reports 'building'"
        echo "            with the latest log line until the vector index is ready."
        echo ""
        echo "  Lexical (BM25) search is available immediately."
        echo "  Vector / hybrid search becomes available when the build completes."
    fi

    # The .mcp.json writer is its own step (write_mcp_config below) so it
    # can also run when indexes=none — keeping the slicer http entry intact.
    write_mcp_config "$level"
}

# Maintain .mcp.json idempotently.  setup.sh owns this file; .mcp.json is
# gitignored because absolute paths in the search-server entry vary per
# machine.  We register:
#
#   slicer              — HTTP endpoint exposed by the in-Slicer WebServer
#                         module (slicer-mcp-server.py).  Static URL.
#
#   slicer-skill-search — stdio search server in this directory; only
#                         registered when level != none.  Removed if level
#                         was previously bm25/hybrid and is now none.
#
# Any user-added entries are preserved.
write_mcp_config() {
    local level="$1"
    local venv_dir="$SCRIPT_DIR/.venv"
    local mcp_config="$SCRIPT_DIR/.mcp.json"
    # Prefer the venv python if it exists; fall back to system python3.
    local py_for_writer
    if [ -x "$venv_dir/bin/python" ]; then
        py_for_writer="$venv_dir/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py_for_writer="python3"
    else
        echo "  python3 not available — skipping .mcp.json update."
        return 0
    fi
    SKILL_PY="$venv_dir/bin/python" \
    SKILL_SCRIPT="$SCRIPT_DIR/slicer-skill-search-mcp.py" \
    SKILL_MCP="$mcp_config" \
    SKILL_INCLUDE_SEARCH="$([ "$level" != "none" ] && echo yes || echo no)" \
    "$py_for_writer" - <<'PY'
import json
import os
from pathlib import Path

mcp_path = Path(os.environ["SKILL_MCP"])
config = {"mcpServers": {}}
if mcp_path.exists():
    try:
        config = json.loads(mcp_path.read_text())
    except json.JSONDecodeError:
        pass
servers = config.setdefault("mcpServers", {})
# Static entry — only add if the user hasn't already configured it
servers.setdefault("slicer", {
    "type": "http",
    "url": "http://localhost:2026/mcp",
})
# Search entry — present iff search indexes are enabled
if os.environ["SKILL_INCLUDE_SEARCH"] == "yes":
    servers["slicer-skill-search"] = {
        "type": "stdio",
        "command": os.environ["SKILL_PY"],
        "args": [os.environ["SKILL_SCRIPT"]],
    }
else:
    servers.pop("slicer-skill-search", None)
mcp_path.write_text(json.dumps(config, indent=2) + "\n")
which = "slicer + slicer-skill-search" if os.environ["SKILL_INCLUDE_SEARCH"] == "yes" else "slicer only (search disabled)"
print(f"  Wrote {mcp_path} ({which})")
PY
}

if [ "$INDEXES" != "none" ]; then
    setup_search_indexes "$INDEXES"
else
    # Still maintain .mcp.json so the slicer http entry stays in sync
    # (and any prior search entry is removed cleanly).
    write_mcp_config "none"
fi

# ─── Write stamp ─────────────────────────────────────────────
write_stamp

echo ""
echo "Setup complete (mode: $MODE, indexes: $INDEXES)."

# ─── Rebase/Update Notes ─────────────────────────────────────
# This script uses git pull --ff-only for updates, which performs fast-forward
# merges when possible. If a repository is in detached HEAD state (e.g., checked
# out to a specific tag), it fetches all branches/tags but skips the pull operation
# to avoid disrupting the detached state. This ensures repositories stay in their
# intended state while still getting updates.
