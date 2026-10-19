# The C++ implementation of PL4G: a library holding the front end, the IR, the
# interpreter, the REPL, the tree-sitter service and the LSP service, and the
# small programs linked with it.  See spec/cpp-implementation.md.
#
# Targets:
#   pl4g              the library, libpl4g.a
#   pl4g-repl         the stand-alone REPL and interpreter
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

set(PL4G_COMMON_FLAGS -fcontracts -freflection -Wall -Wextra -Wno-unused-parameter -Wno-trigraphs)

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

set(PL4G_LIB_SOURCES
  src/lib/util/unicode.cc
  src/lib/util/fdio.cc
  src/lib/util/contracts.cc
  src/lib/source/manager.cc
  src/lib/diag/catalog.cc
  src/lib/diag/engine.cc
  src/lib/diag/render.cc
  src/lib/front/token.cc
  src/lib/front/lexer.cc
  ${PL4G_GENERATED}/diag/catalog_data.cc
)

# Define the library and the programs linked with it, with `suffix` on every
# name and `flags` added to compiling and linking.
function(pl4g_variant suffix)
  set(flags ${ARGN})
  add_library(pl4g${suffix} STATIC ${PL4G_LIB_SOURCES})
  set_target_properties(pl4g${suffix} PROPERTIES OUTPUT_NAME pl4g${suffix})
  target_include_directories(pl4g${suffix} PUBLIC ${CMAKE_SOURCE_DIR}/src/lib ${PL4G_GENERATED})
  target_compile_options(pl4g${suffix} PUBLIC ${PL4G_COMMON_FLAGS} ${flags})
  target_link_options(pl4g${suffix} PUBLIC ${flags})
  target_link_libraries(pl4g${suffix} PUBLIC simdjson::simdjson ${UNISTRING_LIBRARY})

  add_executable(pl4g-lexdump${suffix} src/tools/lexdump.cc)
  target_link_libraries(pl4g-lexdump${suffix} PRIVATE pl4g${suffix})

  # The fuzzing entry points, driven here by a main of our own that replays
  # the inputs it is given -- the corpus in ctest, a crash being reproduced.
  add_executable(pl4g-fuzz-lexer-replay${suffix} src/fuzz/lexer.cc src/fuzz/replay.cc)
  target_link_libraries(pl4g-fuzz-lexer-replay${suffix} PRIVATE pl4g${suffix})
endfunction()

if(PL4G_FUZZ)
  # Built with afl-g++-fast: the library instrumented, driven by AFL++'s
  # driver of libFuzzer-style entry points.
  pl4g_variant("")
  foreach(target lexer)
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
# The fuzzing entry points replayed over the test programs, sanitized.
if(PL4G_SANITIZERS)
  add_test(NAME lexer-agrees-asan
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/lexer_agrees.py
            $<TARGET_FILE:pl4g-lexdump-asan> ${CMAKE_SOURCE_DIR})
  add_test(NAME fuzz-lexer-replay-asan
    COMMAND pl4g-fuzz-lexer-replay-asan ${PL4G_TESTS} ${CMAKE_SOURCE_DIR}/src/fuzz/corpus/lexer)
endif()

# Fuzzing, for PL4G_FUZZ_SECONDS each entry point, after the AFL++ build.
set(PL4G_FUZZ_SECONDS 30 CACHE STRING "How long ctest fuzzes each entry point")
add_test(NAME fuzz-build COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target fuzz)
set_tests_properties(fuzz-build PROPERTIES FIXTURES_SETUP fuzzers LABELS fuzz)
foreach(target lexer)
  add_test(NAME fuzz-${target}
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/src/test/run_fuzz.py ${CMAKE_BINARY_DIR}/fuzz/pl4g-fuzz-${target}
            ${PL4G_FUZZ_SECONDS} ${CMAKE_BINARY_DIR}/fuzz-runs/${target}
            ${CMAKE_SOURCE_DIR}/src/fuzz/corpus/${target} ${PL4G_TESTS})
  set_tests_properties(fuzz-${target} PROPERTIES FIXTURES_REQUIRED fuzzers LABELS fuzz SKIP_RETURN_CODE 77
                       TIMEOUT 600)
endforeach()

add_custom_target(checkCOMMAND ${CMAKE_CTEST_COMMAND} --output-on-failure -j 8 USES_TERMINAL)
