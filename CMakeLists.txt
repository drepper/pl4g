# The C++ implementation of PL4G: a library holding the front end, the IR, the
# interpreter, the REPL, the tree-sitter service and the LSP service, and the
# small programs linked with it.  See spec/cpp-implementation.md.
#
# Targets:
#   pl4g              the library, libpl4g.a
#   pl4g-repl         the stand-alone REPL and interpreter
#   pl4g-lsp          the stand-alone language server
#   pl4g-lexdump      the tokens of a file, for comparing with pypl4g
#   *-asan            the same, built with the address and undefined-behaviour
#                     sanitizers (PL4G_SANITIZERS=ON, the default)
#   fuzz              builds the fuzzing entry points with AFL++ in build/fuzz
#                     (a build of its own, since AFL++ is the compiler there)
#   check             runs ctest
cmake_minimum_required(VERSION 3.30)
project(pl4g LANGUAGES C CXX)

if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR CMAKE_CXX_COMPILER_VERSION VERSION_LESS 16)
  message(FATAL_ERROR "GCC 16 or later is needed: contracts and reflection are used throughout")
endif()

set(CMAKE_CXX_STANDARD 26)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

option(PL4G_SANITIZERS "Also build the sanitizer variants" ON)
# Set by the fuzzing build, which compiles only the fuzzing entry points.
option(PL4G_FUZZ "Build the fuzzing entry points (with AFL++ as the compiler)" OFF)

find_package(simdjson REQUIRED)
find_library(UNISTRING_LIBRARY unistring REQUIRED)
# Line editing for the interactive session, where its input is a terminal.
find_library(EDIT_LIBRARY edit REQUIRED)
# The tree-sitter service: the grammar's parser is built in, the core is the system's.
find_library(TREE_SITTER_LIBRARY tree-sitter REQUIRED)

# The fuzzing entry points, each src/fuzz/NAME.cc with a corpus in src/fuzz/corpus/NAME.
set(PL4G_FUZZ_TARGETS lexer parser check)

set(PL4G_COMMON_FLAGS $<$<COMPILE_LANGUAGE:CXX>:-fcontracts> $<$<COMPILE_LANGUAGE:CXX>:-freflection> -Wall -Wextra
  -Wno-unused-parameter $<$<COMPILE_LANGUAGE:CXX>:-Wno-trigraphs>)

# The diagnostic catalog, generated from the shared JSON file.
add_executable(pl4g-gen-diag src/tools/gen-diag.cc)
target_compile_options(pl4g-gen-diag PRIVATE ${PL4G_COMMON_FLAGS})
target_link_libraries(pl4g-gen-diag PRIVATE simdjson::simdjson ${UNISTRING_LIBRARY})
set(PL4G_GENERATED ${CMAKE_BINARY_DIR}/generated)
add_custom_command(
  OUTPUT ${PL4G_GENERATED}/diag/ids.hh ${PL4G_GENERATED}/diag/catalog_data.cc
  COMMAND ${CMAKE_COMMAND} -E make_directory ${PL4G_GENERATED}/diag
  COMMAND pl4g-gen-diag ${CMAKE_SOURCE_DIR}/share/diagnostics.json
          ${PL4G_GENERATED}/diag/ids.hh ${PL4G_GENERATED}/diag/catalog_data.cc
  DEPENDS pl4g-gen-diag ${CMAKE_SOURCE_DIR}/share/diagnostics.json
  COMMENT "Generating the diagnostic catalog")

# The runtime a compiled program carries, compiled into the library as it is.
set_source_files_properties(runtime/io.c PROPERTIES COMPILE_OPTIONS "-fno-builtin;-fno-strict-aliasing")

set(PL4G_LIB_SOURCES
  src/lib/util/unicode.cc
  src/lib/util/fdio.cc
  src/lib/util/contracts.cc
  src/lib/util/pyfloat.cc
  src/lib/source/manager.cc
  src/lib/diag/catalog.cc
  src/lib/diag/engine.cc
  src/lib/diag/render.cc
  src/lib/diag/style.cc
  src/lib/front/token.cc
  src/lib/front/lexer.cc
  src/lib/front/ast.cc
  src/lib/front/parser.cc
  src/lib/front/dump.cc
  src/lib/ir/types.cc
  src/lib/ir/ir.cc
  src/lib/ir/printer.cc
  src/lib/ir/json.cc
  src/lib/ir/layout.cc
  src/lib/ir/builder.cc
  src/lib/opt/passes.cc
  src/lib/opt/flow.cc
  src/lib/opt/constfold.cc
  src/lib/opt/dce.cc
  src/lib/opt/facts.cc
  src/lib/opt/inline.cc
  src/lib/opt/knownargs.cc
  src/lib/opt/purecalls.cc
  src/lib/opt/samevalues.cc
  src/lib/opt/simplifycfg.cc
  src/lib/rt/arena.cc
  src/lib/rt/blob.cc
  src/lib/rt/blob-aarch64.cc
  src/lib/rt/blob-riscv64.cc
  src/lib/rt/blob-x86_64.cc
  src/lib/interp/interp.cc
  src/lib/driver/compile.cc
  src/lib/repl/lines.cc
  src/lib/repl/repl.cc
  src/lib/repl/session.cc
  src/lib/repl/show.cc
  src/lib/lsp/analysis.cc
  src/lib/lsp/json.cc
  src/lib/lsp/places.cc
  src/lib/lsp/server.cc
  src/lib/lsp/wire.cc
  src/lib/ts/highlight.cc
  tree-sitter-pl4g/src/parser.c
  tree-sitter-pl4g/src/scanner.c
  src/lib/sema/attributes.cc
  src/lib/sema/check-helpers.cc
  src/lib/sema/check-entry.cc
  src/lib/sema/check-collect.cc
  src/lib/sema/check-arenas.cc
  src/lib/sema/check-constants.cc
  src/lib/sema/check-places.cc
  src/lib/sema/check-loops.cc
  src/lib/sema/check-match.cc
  src/lib/sema/check-operators.cc
  src/lib/sema/check-builtins.cc
  src/lib/sema/clauses.cc
  src/lib/sema/environ.cc
  src/lib/sema/lastuse.cc
  src/lib/sema/lists.cc
  src/lib/sema/liveness.cc
  src/lib/sema/modules.cc
  src/lib/sema/notes.cc
  src/lib/sema/owned.cc
  src/lib/sema/strings.cc
  src/lib/sema/tables.cc
  src/lib/front/doccomment.cc
  src/lib/front/expand.cc
  src/lib/front/interpret.cc
  src/lib/comptime/build.cc
  src/lib/comptime/evaluate.cc
  src/lib/target/registry.cc
  src/lib/target/syscalls.cc
  src/lib/ir/verify.cc
  runtime/io.c
  ${PL4G_GENERATED}/diag/catalog_data.cc
)

# Define the library and the programs linked with it, with `suffix` on every
# name and `flags` added to compiling and linking.
function(pl4g_variant suffix)
  set(flags ${ARGN})
  add_library(pl4g${suffix} STATIC ${PL4G_LIB_SOURCES})
  set_target_properties(pl4g${suffix} PROPERTIES OUTPUT_NAME pl4g${suffix})
  target_include_directories(pl4g${suffix} PUBLIC ${CMAKE_SOURCE_DIR}/src/lib ${PL4G_GENERATED})
  target_include_directories(pl4g${suffix} PRIVATE ${CMAKE_SOURCE_DIR}/tree-sitter-pl4g/src)
  # Where #embed finds the grammar's queries.
  set_source_files_properties(src/lib/ts/highlight.cc PROPERTIES
    COMPILE_OPTIONS "--embed-dir=${CMAKE_SOURCE_DIR}/tree-sitter-pl4g/queries")
  target_compile_options(pl4g${suffix} PUBLIC ${PL4G_COMMON_FLAGS} ${flags})
  target_link_options(pl4g${suffix} PUBLIC ${flags})
  target_link_libraries(pl4g${suffix} PUBLIC simdjson::simdjson ${UNISTRING_LIBRARY} ${EDIT_LIBRARY} ${TREE_SITTER_LIBRARY})

  # The stand-alone interpreter: the library and a small driver.
  add_executable(pl4g-repl${suffix} src/repl/main.cc)
  target_link_libraries(pl4g-repl${suffix} PRIVATE pl4g${suffix})
  # The stand-alone language server, the same way.
  add_executable(pl4g-lsp${suffix} src/lsp/main.cc)
  target_link_libraries(pl4g-lsp${suffix} PRIVATE pl4g${suffix})

  foreach(tool lexdump astdump irdump runjson checkdump)
    add_executable(pl4g-${tool}${suffix} src/tools/${tool}.cc)
    target_link_libraries(pl4g-${tool}${suffix} PRIVATE pl4g${suffix})
  endforeach()

  # The fuzzing entry points, driven here by a main of our own that replays
  # the inputs it is given -- the corpus in ctest, a crash being reproduced.
  foreach(target ${PL4G_FUZZ_TARGETS})
    add_executable(pl4g-fuzz-${target}-replay${suffix} src/fuzz/${target}.cc src/fuzz/replay.cc)
    target_link_libraries(pl4g-fuzz-${target}-replay${suffix} PRIVATE pl4g${suffix})
  endforeach()
endfunction()

if(PL4G_FUZZ)
  # Built with afl-g++-fast: the library instrumented, driven by AFL++'s
  # driver of libFuzzer-style entry points.
  pl4g_variant("")
  foreach(target ${PL4G_FUZZ_TARGETS})
    add_executable(pl4g-fuzz-${target} src/fuzz/${target}.cc)
    target_link_libraries(pl4g-fuzz-${target} PRIVATE pl4g /usr/lib64/afl/libAFLDriver.a)
  endforeach()
  return()
endif()

pl4g_variant("")
if(PL4G_SANITIZERS)
  pl4g_variant("-asan" -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
endif()

# The fuzzing build, which has a compiler of its own and so a tree of its own.
add_custom_target(fuzz
  COMMAND ${CMAKE_COMMAND} -S ${CMAKE_SOURCE_DIR} -B ${CMAKE_BINARY_DIR}/fuzz
          -DCMAKE_CXX_COMPILER=afl-g++-fast -DCMAKE_C_COMPILER=afl-gcc-fast
          -DCMAKE_BUILD_TYPE=RelWithDebInfo -DPL4G_FUZZ=ON
  COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR}/fuzz --parallel
  USES_TERMINAL
  COMMENT "Building the fuzzing entry points with AFL++")

enable_testing()
set(PL4G_TESTS ${CMAKE_SOURCE_DIR}/tests/language)

# Every test program lexes as pypl4g lexes it.
add_test(NAME lexer-agrees
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/lexer_agrees.py
          $<TARGET_FILE:pl4g-lexdump> ${CMAKE_SOURCE_DIR})
# Every test program parses to the tree pypl4g makes of it.
add_test(NAME parser-agrees
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/parser_agrees.py
          $<TARGET_FILE:pl4g-astdump> ${CMAKE_SOURCE_DIR})
# Every program pypl4g compiles has, read from JSON, the IR text pypl4g wrote
# and the layout pypl4g computed.
add_test(NAME ir-agrees
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/ir_agrees.py
          $<TARGET_FILE:pl4g-irdump> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/ir-agrees)
set_tests_properties(ir-agrees PROPERTIES TIMEOUT 900)
# The passes of level nought make of what pypl4g's checker made exactly what
# pypl4g's passes make of it.
add_test(NAME passes-agree
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/passes_agree.py
          $<TARGET_FILE:pl4g-irdump> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/passes-agree)
set_tests_properties(passes-agree PROPERTIES TIMEOUT 900)
add_test(NAME runs-agree-after-the-passes
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_agrees.py --passes
          $<TARGET_FILE:pl4g-runjson> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/runs-agree-after-the-passes)
set_tests_properties(runs-agree-after-the-passes PROPERTIES TIMEOUT 1800)
# The same for the optimizations: what pypl4g's checker made, put through the
# passes of level one, is what pypl4g's passes make of it, and runs as the
# tests that ask for level one say.
add_test(NAME passes-agree-at-level-1
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/passes_agree.py --level=1
          $<TARGET_FILE:pl4g-irdump> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/passes-agree-at-level-1)
set_tests_properties(passes-agree-at-level-1 PROPERTIES TIMEOUT 900)
add_test(NAME runs-agree-after-the-passes-at-level-1
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_agrees.py --passes --level=1
          $<TARGET_FILE:pl4g-runjson> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/runs-agree-after-the-passes-at-level-1)
set_tests_properties(runs-agree-after-the-passes-at-level-1 PROPERTIES TIMEOUT 1800)
# Every program that runs, compiled by pypl4g and interpreted here, exits as its
# test says.
add_test(NAME runs-agree
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_agrees.py
          $<TARGET_FILE:pl4g-runjson> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/runs-agree)
set_tests_properties(runs-agree PROPERTIES TIMEOUT 1800)
# Every test program is checked here as pypl4g checks it: the same words on
# standard error, the same status, and the same IR before any pass ran.
add_test(NAME checks-agree
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/check_agrees.py
          $<TARGET_FILE:pl4g-checkdump> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/checks-agree)
set_tests_properties(checks-agree PROPERTIES TIMEOUT 1800)
# The same with colour, which is where the tree-sitter grammar colours each
# snippet as pypl4g's binding of it does.
add_test(NAME checks-agree-in-colour
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/check_agrees.py --with=--color=yes
          $<TARGET_FILE:pl4g-checkdump> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/checks-agree-in-colour)
set_tests_properties(checks-agree-in-colour PROPERTIES TIMEOUT 1800)
# And every program that runs, run from its source with nothing of pypl4g's in
# the way, exits as its test says.
add_test(NAME runs-from-source
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/interp_runs.py $<TARGET_FILE:pl4g-repl> ${CMAKE_SOURCE_DIR})
set_tests_properties(runs-from-source PROPERTIES TIMEOUT 1800)
# The language server answers every conversation of tests/compiler/test_lsp.py
# as pypl4g's does.
add_test(NAME lsp-agrees
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/lsp_agrees.py $<TARGET_FILE:pl4g-lsp> ${CMAKE_SOURCE_DIR})
set_tests_properties(lsp-agrees PROPERTIES TIMEOUT 900)
# The interactive session answers typed sessions as it should.
add_test(NAME repl-sessions
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/repl_sessions.py $<TARGET_FILE:pl4g-repl> ${CMAKE_SOURCE_DIR})
# The same, sanitized, and the fuzzing entry points replayed over the test
# programs and the corpora under the sanitizers.
if(PL4G_SANITIZERS)
  foreach(stage lexer parser)
    set(tool lexdump)
    if(stage STREQUAL "parser")
      set(tool astdump)
    endif()
    add_test(NAME ${stage}-agrees-asan
      COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/${stage}_agrees.py
              $<TARGET_FILE:pl4g-${tool}-asan> ${CMAKE_SOURCE_DIR})
  endforeach()
  add_test(NAME runs-agree-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_agrees.py
            $<TARGET_FILE:pl4g-runjson-asan> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/runs-agree-asan)
  set_tests_properties(runs-agree-asan PROPERTIES TIMEOUT 1800)
  add_test(NAME passes-agree-at-level-1-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/passes_agree.py --level=1
            $<TARGET_FILE:pl4g-irdump-asan> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/passes-agree-at-level-1-asan)
  set_tests_properties(passes-agree-at-level-1-asan PROPERTIES TIMEOUT 1800)
  add_test(NAME runs-agree-after-the-passes-at-level-1-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_agrees.py --passes --level=1
            $<TARGET_FILE:pl4g-runjson-asan> ${CMAKE_SOURCE_DIR}
            ${CMAKE_BINARY_DIR}/runs-agree-after-the-passes-at-level-1-asan)
  set_tests_properties(runs-agree-after-the-passes-at-level-1-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME checks-agree-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/check_agrees.py
            $<TARGET_FILE:pl4g-checkdump-asan> ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/checks-agree-asan)
  set_tests_properties(checks-agree-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME runs-from-source-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/interp_runs.py $<TARGET_FILE:pl4g-repl-asan> ${CMAKE_SOURCE_DIR})
  set_tests_properties(runs-from-source-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME lsp-agrees-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/lsp_agrees.py $<TARGET_FILE:pl4g-lsp-asan> ${CMAKE_SOURCE_DIR})
  set_tests_properties(lsp-agrees-asan PROPERTIES TIMEOUT 1800)
  add_test(NAME repl-sessions-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/repl_sessions.py $<TARGET_FILE:pl4g-repl-asan> ${CMAKE_SOURCE_DIR})
  foreach(target ${PL4G_FUZZ_TARGETS})
    add_test(NAME fuzz-${target}-replay-asan
      COMMAND pl4g-fuzz-${target}-replay-asan ${PL4G_TESTS} ${CMAKE_SOURCE_DIR}/src/fuzz/corpus/${target})
  endforeach()
endif()

# Fuzzing, for PL4G_FUZZ_SECONDS each entry point, after the AFL++ build.
set(PL4G_FUZZ_SECONDS 30 CACHE STRING "How long ctest fuzzes each entry point")
add_test(NAME fuzz-build COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target fuzz)
set_tests_properties(fuzz-build PROPERTIES FIXTURES_SETUP fuzzers LABELS fuzz)
foreach(target ${PL4G_FUZZ_TARGETS})
  add_test(NAME fuzz-${target}
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_fuzz.py ${CMAKE_BINARY_DIR}/fuzz/pl4g-fuzz-${target}
            ${PL4G_FUZZ_SECONDS} ${CMAKE_BINARY_DIR}/fuzz-runs/${target}
            ${CMAKE_SOURCE_DIR}/src/fuzz/corpus/${target} ${PL4G_TESTS})
  set_tests_properties(fuzz-${target} PROPERTIES FIXTURES_REQUIRED fuzzers LABELS fuzz SKIP_RETURN_CODE 77
                       TIMEOUT 600)
endforeach()

add_custom_target(check COMMAND ${CMAKE_CTEST_COMMAND} --output-on-failure -j 8 USES_TERMINAL)
