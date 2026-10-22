# The C++ implementation of PL4G: a library holding the front end, the IR, the
# interpreter, the REPL, the tree-sitter service and the LSP service, and the
# small programs linked with it.  See spec/cpp-implementation.md.
#
# Targets:
#   pl4g              the library, libpl4g.a
#   pl4g-repl         the stand-alone REPL and interpreter
#   pl4g-lsp          the stand-alone language server
#   pl4g-embed-host   a program embedding the interpreter, whose functions
#                     the programs it runs call (a test)
#   pl4g-embed        the interpreter as a shared object, libpl4g-embed.so,
#                     offering pl4g_embed_run_source with C's linkage
#   pl4g-embed-example  a C program that loads it with dlopen and runs a
#                     program calling back into it (src/examples/embed)
#   pl4g-embed-example-cc  the same in C++, through embed/pl4g-embed.hh
#   pl4g-embed-defined  a host handing PL4G its functions by name, not dlsym
#   pl4g-embed        the interpreter as libpl4g-embed.so and libpl4g-embed.a
#   pl4g-manual       the manual, a node at a time (libpl4g-manual.so, and
#                     libpl4g-manual.a), and pl4g-help, which shows one
#   pl4g-embed-expressions  expressions as JSON evaluated in PL4G, the replies
#                     checked with nlohmann::json and json-schema-validator
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
# Every object is position-independent code, -fPIC and not -fPIE also where it
# ends up in a program: what is in an archive may go into a shared object, and
# one flag for all of it means no object has to be compiled twice for that.
# CMake's own POSITION_INDEPENDENT_CODE is not used: it says -fPIE of a
# program's objects, after this, and the last flag is the one that counts.
add_compile_options(-fPIC)
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
# What the interpreter calls a function the host defines through: the call
# described from the function's signature and made as the system makes it.
find_package(PkgConfig REQUIRED)
pkg_check_modules(FFI REQUIRED IMPORTED_TARGET libffi)

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

# The runtime a compiled program carries, compiled into the library as it is
# but for where it keeps what it remembers: in the state the interpreter
# selects (runtime/state.h) rather than in variables of its own.
set_source_files_properties(runtime/io.c PROPERTIES COMPILE_OPTIONS "-fno-builtin;-fno-strict-aliasing"
  COMPILE_DEFINITIONS PL4G_RUNTIME_STATE)

set(PL4G_LIB_SOURCES
  src/lib/util/unicode.cc
  src/lib/util/fdio.cc
  src/lib/util/contracts.cc
  src/lib/util/pyfloat.cc
  src/lib/util/pyrepr.cc
  src/lib/util/sha256.cc
  src/lib/source/manager.cc
  src/lib/diag/catalog.cc
  src/lib/diag/engine.cc
  src/lib/diag/render.cc
  src/lib/diag/style.cc
  src/lib/front/token.cc
  src/lib/front/lexer.cc
  src/lib/front/ast.cc
  src/lib/front/parser.cc
  src/lib/front/repr.cc
  src/lib/front/dump.cc
  src/lib/ir/types.cc
  src/lib/ir/ir.cc
  src/lib/ir/printer.cc
  src/lib/ir/json.cc
  src/lib/ir/layout.cc
  src/lib/ir/builder.cc
  src/lib/opt/passes.cc
  src/lib/opt/flow.cc
  src/lib/opt/conditions.cc
  src/lib/opt/constfold.cc
  src/lib/opt/dce.cc
  src/lib/opt/divisions.cc
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
  src/lib/embed/embed.cc
  src/lib/mc/asmbuilder.cc
  src/lib/mc/desc.cc
  src/lib/mc/dump.cc
  src/lib/mc/fixedwidth.cc
  src/lib/mc/fixup.cc
  src/lib/mc/inst.cc
  src/lib/mc/layout.cc
  src/lib/mc/loops.cc
  src/lib/mc/machine.cc
  src/lib/mc/operand.cc
  src/lib/mc/ops.cc
  src/lib/mc/reg.cc
  src/lib/mc/regalloc.cc
  src/lib/mc/streamer.cc
  src/lib/elf/dynamic.cc
  src/lib/elf/relr.cc
  src/lib/elf/strtab.cc
  src/lib/elf/structs.cc
  src/lib/elf/writer.cc
  src/lib/driver/cli.cc
  src/lib/driver/compile.cc
  src/lib/driver/driver.cc
  src/lib/driver/options.cc
  src/lib/driver/sbom.cc
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
  src/lib/target/allocator.cc
  src/lib/target/backtrace.cc
  src/lib/target/bitcount.cc
  src/lib/target/branches.cc
  src/lib/target/callconv.cc
  src/lib/target/faults.cc
  src/lib/target/frames.cc
  src/lib/target/globals.cc
  src/lib/target/host.cc
  src/lib/target/narrow.cc
  src/lib/target/relocate.cc
  src/lib/target/ordering.cc
  src/lib/target/pool.cc
  src/lib/target/registry.cc
  src/lib/target/runs.cc
  src/lib/target/runtime.cc
  src/lib/target/saturate.cc
  src/lib/target/shared.cc
  src/lib/target/stack.cc
  src/lib/target/started.cc
  src/lib/target/aarch64/abi.cc
  src/lib/target/aarch64/fixups.cc
  src/lib/target/aarch64/isel.cc
  src/lib/target/aarch64/opcodes.cc
  src/lib/target/aarch64/regs.cc
  src/lib/target/aarch64/startup.cc
  src/lib/target/aarch64/target.cc
  src/lib/target/riscv64/abi.cc
  src/lib/target/riscv64/attributes.cc
  src/lib/target/riscv64/fixups.cc
  src/lib/target/riscv64/isa.cc
  src/lib/target/riscv64/isel.cc
  src/lib/target/riscv64/opcodes.cc
  src/lib/target/riscv64/peephole.cc
  src/lib/target/riscv64/regs.cc
  src/lib/target/riscv64/startup.cc
  src/lib/target/riscv64/target.cc
  src/lib/target/x86_64/abi.cc
  src/lib/target/x86_64/encoder.cc
  src/lib/target/x86_64/isel.cc
  src/lib/target/x86_64/levels.cc
  src/lib/target/x86_64/lower.cc
  src/lib/target/x86_64/opcodes.cc
  src/lib/target/x86_64/ops.cc
  src/lib/target/x86_64/peephole.cc
  src/lib/target/x86_64/regs.cc
  src/lib/target/x86_64/startup.cc
  src/lib/target/x86_64/target.cc
  src/lib/target/syscalls.cc
  src/lib/target/target.cc
  src/lib/target/tests.cc
  src/lib/target/vectors.cc
  src/lib/ir/verify.cc
  runtime/io.c
  ${PL4G_GENERATED}/diag/catalog_data.cc
)

# What the library is linked with, wherever it ends up.
set(PL4G_LIB_LINKS simdjson::simdjson ${UNISTRING_LIBRARY} ${EDIT_LIBRARY} ${TREE_SITTER_LIBRARY} PkgConfig::FFI
    ${CMAKE_DL_LIBS})

# What every build of the library is compiled with: where its headers are,
# what #embed reads, and what it links with.
function(pl4g_library_settings target)
  set(flags ${ARGN})
  target_include_directories(${target} PUBLIC ${CMAKE_SOURCE_DIR}/src/lib ${PL4G_GENERATED})
  target_include_directories(${target} PRIVATE ${CMAKE_SOURCE_DIR}/tree-sitter-pl4g/src ${CMAKE_SOURCE_DIR}/runtime)
  # Where #embed finds the grammar's queries.
  set_source_files_properties(src/lib/ts/highlight.cc PROPERTIES
    COMPILE_OPTIONS "--embed-dir=${CMAKE_SOURCE_DIR}/tree-sitter-pl4g/queries")
  # And where it finds the shared option table.
  set_source_files_properties(src/lib/driver/options.cc PROPERTIES
    COMPILE_OPTIONS "--embed-dir=${CMAKE_SOURCE_DIR}/share")
  target_compile_options(${target} PUBLIC ${PL4G_COMMON_FLAGS} ${flags})
  target_link_options(${target} PUBLIC ${flags})
  target_link_libraries(${target} PUBLIC ${PL4G_LIB_LINKS})
endfunction()

# Define the library and the programs linked with it, with `suffix` on every
# name and `flags` added to compiling and linking.
function(pl4g_variant suffix)
  set(flags ${ARGN})
  add_library(pl4g${suffix} STATIC ${PL4G_LIB_SOURCES})
  set_target_properties(pl4g${suffix} PROPERTIES OUTPUT_NAME pl4g${suffix})
  pl4g_library_settings(pl4g${suffix} ${flags})

  # The stand-alone interpreter: the library and a small driver.
  add_executable(pl4g-repl${suffix} src/repl/main.cc)
  target_link_libraries(pl4g-repl${suffix} PRIVATE pl4g${suffix})
  # The compiler: the library and its command line, a program called pl4g.
  add_executable(pl4g-compiler${suffix} src/compiler/main.cc)
  set_target_properties(pl4g-compiler${suffix} PROPERTIES OUTPUT_NAME pl4g${suffix})
  target_link_libraries(pl4g-compiler${suffix} PRIVATE pl4g${suffix})
  # The stand-alone language server, the same way.
  add_executable(pl4g-lsp${suffix} src/lsp/main.cc)
  target_link_libraries(pl4g-lsp${suffix} PRIVATE pl4g${suffix})

  foreach(tool lexdump astdump irdump runjson checkdump mcdump vecdump codegen encdump isadump)
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
# The machine-code layer and the ELF writer make of programs of builder calls,
# with a made-up target, exactly what pypl4g's make of them.
add_test(NAME mc-agrees
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/mc_agrees.py $<TARGET_FILE:pl4g-mcdump> 2000 1)
# A run of elements comes apart into what each target's vector registers can
# do exactly as pypl4g's back ends take it apart.
add_test(NAME vectors-agree
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/vectors_agree.py $<TARGET_FILE:pl4g-vecdump> ${CMAKE_SOURCE_DIR}
          ${CMAKE_BINARY_DIR}/vectors-agree)
set_tests_properties(vectors-agree PROPERTIES TIMEOUT 1800)
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
# The compiler's driver answers every command line and every --emit that needs
# no code generated as pypl4g's does.
add_test(NAME driver-agrees
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/driver_agrees.py $<TARGET_FILE:pl4g-compiler> ${CMAKE_SOURCE_DIR}
          ${CMAKE_BINARY_DIR}/driver-agrees)
set_tests_properties(driver-agrees PROPERTIES TIMEOUT 1800)
# The interactive session answers typed sessions as it should.
add_test(NAME repl-sessions
  COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/repl_sessions.py $<TARGET_FILE:pl4g-repl> ${CMAKE_SOURCE_DIR})
# A program embedding the interpreter runs programs that call the functions it
# defines, every kind of value crossing both ways, and one that calls what it
# does not define.  Its symbols are exported, as an embedding program's have
# to be for dlsym to find them.
# The interpreter as a shared object a program loads with dlopen, offering
# one function with C's linkage (src/lib/embed/pl4g-embed.h) and hiding the
# rest; and the example of a program doing so, whose functions the program it
# runs calls, which is why its symbols are exported.
#
# Its objects are compiled once and are both the shared object and an archive,
# libpl4g-embed.a, for a program that carries the interpreter rather than
# loading it: the archive holds the same C entry and the same C++ interface,
# and passes on what it is linked with to what links it.
add_library(pl4g-embed-objects OBJECT ${PL4G_LIB_SOURCES} src/lib/embed/c-entry.cc)
set_target_properties(pl4g-embed-objects PROPERTIES C_VISIBILITY_PRESET hidden CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN ON)
pl4g_library_settings(pl4g-embed-objects)
add_library(pl4g-embed SHARED $<TARGET_OBJECTS:pl4g-embed-objects>)
target_link_libraries(pl4g-embed PRIVATE ${PL4G_LIB_LINKS})
target_link_options(pl4g-embed PRIVATE -Wl,--version-script=${CMAKE_SOURCE_DIR}/src/lib/embed/pl4g-embed.map)
set_target_properties(pl4g-embed PROPERTIES LINK_DEPENDS ${CMAKE_SOURCE_DIR}/src/lib/embed/pl4g-embed.map)
add_library(pl4g-embed-static STATIC $<TARGET_OBJECTS:pl4g-embed-objects>)
set_target_properties(pl4g-embed-static PROPERTIES OUTPUT_NAME pl4g-embed)
target_include_directories(pl4g-embed-static PUBLIC ${CMAKE_SOURCE_DIR}/src/lib)
target_link_libraries(pl4g-embed-static PUBLIC ${PL4G_LIB_LINKS})
add_executable(pl4g-embed-example src/examples/embed/host.c)
target_include_directories(pl4g-embed-example PRIVATE ${CMAKE_SOURCE_DIR}/src/lib)
target_link_libraries(pl4g-embed-example PRIVATE ${CMAKE_DL_LIBS})
set_target_properties(pl4g-embed-example PROPERTIES ENABLE_EXPORTS ON)
add_dependencies(pl4g-embed-example pl4g-embed)
add_test(NAME embed-example
  COMMAND prlimit --data=536870912 --cpu=120 -- $<TARGET_FILE:pl4g-embed-example> $<TARGET_FILE:pl4g-embed>)
# And the same in C++, through the object embed/pl4g-embed.hh wraps it in.
add_executable(pl4g-embed-example-cc src/examples/embed/host.cc)
target_include_directories(pl4g-embed-example-cc PRIVATE ${CMAKE_SOURCE_DIR}/src/lib)
# With its contracts, which are reported by what -fcontracts links.
target_compile_options(pl4g-embed-example-cc PRIVATE -Wall -Wextra -fcontracts)
target_link_options(pl4g-embed-example-cc PRIVATE -fcontracts)
target_link_libraries(pl4g-embed-example-cc PRIVATE ${CMAKE_DL_LIBS})
set_target_properties(pl4g-embed-example-cc PROPERTIES ENABLE_EXPORTS ON)
add_dependencies(pl4g-embed-example-cc pl4g-embed)
add_test(NAME embed-example-cc
  COMMAND prlimit --data=536870912 --cpu=120 -- $<TARGET_FILE:pl4g-embed-example-cc> $<TARGET_FILE:pl4g-embed>)
# And the host handing its functions over by name rather than exporting them:
# linked without -rdynamic, so that dlsym could not find them.
add_executable(pl4g-embed-defined src/examples/embed/defined.cc)
target_include_directories(pl4g-embed-defined PRIVATE ${CMAKE_SOURCE_DIR}/src/lib)
target_compile_options(pl4g-embed-defined PRIVATE -Wall -Wextra -fcontracts)
target_link_options(pl4g-embed-defined PRIVATE -fcontracts)
target_link_libraries(pl4g-embed-defined PRIVATE ${CMAKE_DL_LIBS})
add_dependencies(pl4g-embed-defined pl4g-embed)
add_test(NAME embed-defined
  COMMAND prlimit --data=536870912 --cpu=120 -- $<TARGET_FILE:pl4g-embed-defined> $<TARGET_FILE:pl4g-embed>)

# Expressions handed over as JSON and evaluated in PL4G, the replies read with
# nlohmann::json and checked against a schema with json-schema-validator;
# built only where both are installed.
find_package(nlohmann_json QUIET)
find_package(nlohmann_json_schema_validator QUIET)
if(nlohmann_json_FOUND AND nlohmann_json_schema_validator_FOUND)
  add_executable(pl4g-embed-expressions src/examples/embed/expressions.cc)
  target_include_directories(pl4g-embed-expressions PRIVATE ${CMAKE_SOURCE_DIR}/src/lib)
  target_compile_options(pl4g-embed-expressions PRIVATE -Wall -Wextra -fcontracts)
  target_link_options(pl4g-embed-expressions PRIVATE -fcontracts)
  target_link_libraries(pl4g-embed-expressions PRIVATE nlohmann_json::nlohmann_json nlohmann_json_schema_validator
    ${CMAKE_DL_LIBS})
  set_target_properties(pl4g-embed-expressions PROPERTIES ENABLE_EXPORTS ON)
  add_dependencies(pl4g-embed-expressions pl4g-embed)
  add_test(NAME embed-expressions
    COMMAND prlimit --data=1073741824 --cpu=300 -- $<TARGET_FILE:pl4g-embed-expressions> $<TARGET_FILE:pl4g-embed>)
endif()

# The manual: bin/pl4g-manual makes its nodes -- from share/manual/ and from the
# modules' own comments -- and checks the graph they make, every time anything
# it reads changes, and the library embeds what it made.  Standalone: nothing
# of the compiler is in it.
file(GLOB PL4G_MANUAL_SOURCES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/share/manual/*.txt
     ${CMAKE_SOURCE_DIR}/modules/*.pl4g)
set(PL4G_MANUAL_DIR ${CMAKE_BINARY_DIR}/manual)
add_custom_command(OUTPUT ${PL4G_MANUAL_DIR}/manual.pack ${PL4G_MANUAL_DIR}/manual.stamp.hh
  COMMAND ${CMAKE_SOURCE_DIR}/bin/pl4g-manual --out ${PL4G_MANUAL_DIR}
  DEPENDS ${PL4G_MANUAL_SOURCES} ${CMAKE_SOURCE_DIR}/bin/pl4g-manual ${CMAKE_SOURCE_DIR}/pypl4g/front/token.py
          ${CMAKE_SOURCE_DIR}/pypl4g/front/doccomment.py ${CMAKE_SOURCE_DIR}/pypl4g/sema/attributes.py
  COMMENT "Making the manual")
add_custom_target(pl4g-manual-text DEPENDS ${PL4G_MANUAL_DIR}/manual.pack)
# Compiled once, position-independent, and made both a shared object and an
# archive of the same objects: a program may load the manual or carry it.
add_library(pl4g-manual-objects OBJECT src/manual/manual.cc)
target_include_directories(pl4g-manual-objects PUBLIC ${CMAKE_SOURCE_DIR}/src)
# The stamp is included, and the pack embedded, from where the script made them.
target_include_directories(pl4g-manual-objects PRIVATE ${PL4G_MANUAL_DIR})
target_compile_options(pl4g-manual-objects PRIVATE ${PL4G_COMMON_FLAGS} --embed-dir=${PL4G_MANUAL_DIR})
set_source_files_properties(src/manual/manual.cc PROPERTIES OBJECT_DEPENDS
                            "${PL4G_MANUAL_DIR}/manual.pack;${PL4G_MANUAL_DIR}/manual.stamp.hh")
add_dependencies(pl4g-manual-objects pl4g-manual-text)
add_library(pl4g-manual SHARED $<TARGET_OBJECTS:pl4g-manual-objects>)
target_include_directories(pl4g-manual PUBLIC ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(pl4g-manual PRIVATE fstrcmp)
# The archive leaves fstrcmp to whatever links it, saying so.
add_library(pl4g-manual-static STATIC $<TARGET_OBJECTS:pl4g-manual-objects>)
set_target_properties(pl4g-manual-static PROPERTIES OUTPUT_NAME pl4g-manual)
target_include_directories(pl4g-manual-static PUBLIC ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(pl4g-manual-static PUBLIC fstrcmp)
add_executable(pl4g-help src/manual/help.cc)
target_link_libraries(pl4g-help PRIVATE pl4g-manual)
add_executable(pl4g-manual-graph src/manual/graph.cc)
# The check walks the graph through the archive, and pl4g-help reads it
# through the shared object, so that both are used.
target_link_libraries(pl4g-manual-graph PRIVATE pl4g-manual-static)
add_test(NAME manual-graph COMMAND $<TARGET_FILE:pl4g-manual-graph>)

add_executable(pl4g-embed-host src/test/embed_host.cc)
# Through the archive, which is how the archive is used.
target_link_libraries(pl4g-embed-host PRIVATE pl4g-embed-static)
set_target_properties(pl4g-embed-host PROPERTIES ENABLE_EXPORTS ON)
add_test(NAME embed-host COMMAND prlimit --data=536870912 --cpu=120 -- $<TARGET_FILE:pl4g-embed-host>)
# The AArch64 back end generates for every test program, at every level its
# test asks for, what pypl4g's generates, and every row of its encoding table
# encodes as pypl4g's does.
add_test(NAME codegen-agrees-aarch64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py $<TARGET_FILE:pl4g-codegen> ${CMAKE_SOURCE_DIR}
          ${CMAKE_BINARY_DIR}/codegen-agrees-aarch64 aarch64-linux-none)
set_tests_properties(codegen-agrees-aarch64 PROPERTIES TIMEOUT 1800)
# The same for x86-64, at the level it is built for unless it says otherwise.
add_test(NAME codegen-agrees-x86_64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py $<TARGET_FILE:pl4g-codegen> ${CMAKE_SOURCE_DIR}
          ${CMAKE_BINARY_DIR}/codegen-agrees-x86_64 x86_64-linux-none)
set_tests_properties(codegen-agrees-x86_64 PROPERTIES TIMEOUT 1800)
add_test(NAME encoding-agrees-x86_64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/encoding_agrees_x86_64.py $<TARGET_FILE:pl4g-encdump>
          ${CMAKE_SOURCE_DIR})
add_test(NAME encoding-agrees-aarch64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/encoding_agrees_aarch64.py $<TARGET_FILE:pl4g-encdump>
          ${CMAKE_SOURCE_DIR})
# The same for RISC-V: once for the profile it is built for unless told
# otherwise and once for a machine without Zfa, which is the only way the
# other rounding is reached; every row of its encoding table and every fixup
# its tests store; and every ISA string its tests read.
add_test(NAME codegen-agrees-riscv64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py $<TARGET_FILE:pl4g-codegen> ${CMAKE_SOURCE_DIR}
          ${CMAKE_BINARY_DIR}/codegen-agrees-riscv64 riscv64-linux-none)
set_tests_properties(codegen-agrees-riscv64 PROPERTIES TIMEOUT 1800)
add_test(NAME codegen-agrees-riscv64-rv64gc
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py --mclevel=rv64gc $<TARGET_FILE:pl4g-codegen>
          ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/codegen-agrees-riscv64-rv64gc riscv64-linux-none)
set_tests_properties(codegen-agrees-riscv64-rv64gc PROPERTIES TIMEOUT 1800)
add_test(NAME encoding-agrees-riscv64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/encoding_agrees_riscv64.py $<TARGET_FILE:pl4g-encdump>
          ${CMAKE_SOURCE_DIR})
add_test(NAME isa-agrees-riscv64
  COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/isa_agrees_riscv64.py $<TARGET_FILE:pl4g-isadump> ${CMAKE_SOURCE_DIR})
# The C++ compiler writes for every test program, on every architecture and
# at every level its test asks for, the image pypl4g writes, byte for byte.
foreach(triple x86_64-linux-none aarch64-linux-none riscv64-linux-none)
  add_test(NAME elf-agrees-${triple}
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/elf_agrees.py $<TARGET_FILE:pl4g-compiler> ${CMAKE_SOURCE_DIR}
            ${CMAKE_BINARY_DIR}/elf-agrees-${triple} ${triple})
  set_tests_properties(elf-agrees-${triple} PROPERTIES TIMEOUT 1800)
endforeach()
# And pypl4g's own test suite -- every language test, built and run natively
# and under QEMU, and every compiler test that asks the command line -- passes
# with the C++ compiler in pypl4g's place.
add_test(NAME suite-with-pl4g
  COMMAND ${CMAKE_COMMAND} -E env PL4G_COMPILER=$<TARGET_FILE:pl4g-compiler>
          python3 -m pytest -q ${CMAKE_SOURCE_DIR}/tests
  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
# Run on its own: pytest uses every core already, and its tests time
# themselves.
set_tests_properties(suite-with-pl4g PROPERTIES TIMEOUT 3600 RUN_SERIAL TRUE)
# With -fpie, every test program's image is a static position-independent
# executable whose table of relative relocations says what binutils reads out
# of it, and pypl4g's language suite -- every program built so, run natively
# and under QEMU, and read by eu-elflint -- passes.
foreach(triple x86_64-linux-none aarch64-linux-none riscv64-linux-none)
  add_test(NAME pie-agrees-${triple}
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/pie_agrees.py $<TARGET_FILE:pl4g-compiler> ${CMAKE_SOURCE_DIR}
            ${CMAKE_BINARY_DIR}/pie-agrees-${triple} ${triple})
  set_tests_properties(pie-agrees-${triple} PROPERTIES TIMEOUT 1800)
endforeach()
add_test(NAME suite-with-pl4g-pie
  COMMAND ${CMAKE_COMMAND} -E env PL4G_COMPILER=$<TARGET_FILE:pl4g-compiler> PL4G_COMPILER_ARGS=-fpie
          python3 -m pytest -q ${CMAKE_SOURCE_DIR}/tests/language
  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
set_tests_properties(suite-with-pl4g-pie PROPERTIES TIMEOUT 3600 RUN_SERIAL TRUE)
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
  add_test(NAME mc-agrees-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/mc_agrees.py $<TARGET_FILE:pl4g-mcdump-asan> 500 2)
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
  add_test(NAME driver-agrees-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/driver_agrees.py $<TARGET_FILE:pl4g-compiler-asan>
            ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/driver-agrees-asan)
  set_tests_properties(driver-agrees-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME repl-sessions-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/repl_sessions.py $<TARGET_FILE:pl4g-repl-asan> ${CMAKE_SOURCE_DIR})
  add_test(NAME codegen-agrees-aarch64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py $<TARGET_FILE:pl4g-codegen-asan>
            ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/codegen-agrees-aarch64-asan aarch64-linux-none)
  set_tests_properties(codegen-agrees-aarch64-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME codegen-agrees-x86_64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py $<TARGET_FILE:pl4g-codegen-asan>
            ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/codegen-agrees-x86_64-asan x86_64-linux-none)
  set_tests_properties(codegen-agrees-x86_64-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME encoding-agrees-x86_64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/encoding_agrees_x86_64.py $<TARGET_FILE:pl4g-encdump-asan>
            ${CMAKE_SOURCE_DIR})
  add_test(NAME encoding-agrees-aarch64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/encoding_agrees_aarch64.py $<TARGET_FILE:pl4g-encdump-asan>
            ${CMAKE_SOURCE_DIR})
  add_test(NAME codegen-agrees-riscv64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/codegen_agrees.py $<TARGET_FILE:pl4g-codegen-asan>
            ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/codegen-agrees-riscv64-asan riscv64-linux-none)
  set_tests_properties(codegen-agrees-riscv64-asan PROPERTIES TIMEOUT 3600)
  add_test(NAME encoding-agrees-riscv64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/encoding_agrees_riscv64.py $<TARGET_FILE:pl4g-encdump-asan>
            ${CMAKE_SOURCE_DIR})
  add_test(NAME isa-agrees-riscv64-asan
    COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/isa_agrees_riscv64.py $<TARGET_FILE:pl4g-isadump-asan>
            ${CMAKE_SOURCE_DIR})
  foreach(triple x86_64-linux-none aarch64-linux-none riscv64-linux-none)
    add_test(NAME pie-agrees-${triple}-asan
      COMMAND python3 ${CMAKE_SOURCE_DIR}/src/test/pie_agrees.py $<TARGET_FILE:pl4g-compiler-asan>
              ${CMAKE_SOURCE_DIR} ${CMAKE_BINARY_DIR}/pie-agrees-${triple}-asan ${triple})
    set_tests_properties(pie-agrees-${triple}-asan PROPERTIES TIMEOUT 3600)
  endforeach()
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
