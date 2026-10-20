// The compiler, as a library function and as a command line: pypl4g's
// driver/main.py.
//
// It owns the diagnostic engine and the source manager, sequences the stages,
// and is the one place that turns an internal failure into a report rather
// than a crash.  Everything is written to the descriptors it is handed --
// the result to its own, diagnostics and the compiler's other output to
// theirs -- never to 0, 1 and 2 by assumption.
#pragma once


#include <memory>
#include <span>
#include <string>

#include "diag/engine.hh"
#include "driver/options.hh"
#include "sema/notes.hh"
#include "source/manager.hh"


namespace pl4g::driver {

  // Where the compiler reads and writes what is not the result: what it says
  // (`error`), what `--help` and its kind print (`output`), and what the
  // language server reads its requests from (`input`).
  struct Descriptors {
    int input = 0;
    int output = 1;
    int error = 2;
  };

  // Compile what `options` says, writing the result -- the image, or what
  // `--emit` asked for -- into `result`, and answer the status the compiler
  // exits with.  A build function's artifacts are compiled too and written
  // where it named them; `result` is the image of a program that is one.
  int compile(const Options& options, int result, const Descriptors& io);

  // Compile what `options` says the whole way, code generation included, for
  // what that says rather than for the image, which goes nowhere: what the
  // language server does when a file is saved.  Diagnostics go to `diags`,
  // files are read through `sources` -- which may hold what an editor has not
  // saved -- and `notes`, where given, is told what each name is.  `kept`,
  // where given, is handed what holds the trees the run read, which a caller
  // reading them afterwards keeps as long as it does.
  int compile_whole(const Options& options, DiagEngine& diags, SourceManager& sources, sema::Notes* notes,
                    const Descriptors& io, std::shared_ptr<void>* kept = nullptr);

  // Run the compiler over a command line, `argv` without the program's name,
  // as the `pl4g` program does: what it writes goes where `options.output`
  // names, or where the sources suggest.  Answers the status to exit with.
  int run_command_line(std::span<const std::string> argv, const Descriptors& io);

} // namespace pl4g::driver
