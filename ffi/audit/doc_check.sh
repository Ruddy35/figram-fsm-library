#!/usr/bin/env bash
# D-05/T-12/P-22/P-23: проверка согласованности документации с кодом (код 0 — согласовано).
cd "$(dirname "$0")/../.."
f=0
no()  { if grep -nE "$2" $1; then echo "НЕТ: в $1 осталось «$2»"; f=1; else echo "ok: в $1 нет «$2»"; fi; }
yes() { if grep -qE "$2" $1; then echo "ok: в $1 есть «$2»"; else echo "НЕТ: в $1 нет «$2»"; f=1; fi; }
no README.md 'зависимости — только libc'
no Figram/Api.lean 'lean_mark_persistent'
yes Figram/Api.lean 'lean_mark_mt'
yes README.md '^## Встраивание'
yes README.md 'R_X86_64_TPOFF32'
yes README.md 'не видят кучу Lean'
yes README.md 'RLIMIT_AS'
yes README.md 'свой дескриптор'
for w in SIGPIPE sigaltstack io_uring 'RLIMIT_AS' 'mimalloc' 'отдельный дескриптор' 'НЕОПРЕДЕЛЁННОЕ' 'figram_load_doc_ex' 'figram_set_max_request'; do yes ffi/figram.h "$w"; done
# Каждая функция из figram.h определена в figram_ffi.c с FIGRAM_API, и наоборот.
h=$(grep -oE 'FIGRAM_EXPORT [a-z_ *]+\bfigram_[a-z_]+' ffi/figram.h | grep -oE 'figram_[a-z_]+$' | sort -u)
c=$(grep -oE '^FIGRAM_API [a-z_ *]+\bfigram_[a-z_]+' ffi/figram_ffi.c | grep -oE 'figram_[a-z_]+$' | sort -u)
if [ "$h" = "$c" ]; then echo "ok: figram.h и figram_ffi.c экспортируют одни и те же $(echo $h | wc -w) функций"; else echo "НЕТ: расхождение экспорта"; diff <(echo "$h") <(echo "$c"); f=1; fi
exit $f
