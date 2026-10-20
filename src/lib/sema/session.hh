// What the interactive session asks of the checker beyond what a program does.
//
// The session runs each input as a program of its own: what was defined
// before, the variables it has bound so far, and a function made for the
// input.  Three things differ from a program written in a file, and only
// these: a variable of the session is given no value where it is declared --
// the session puts back what it held; a name the input binds at its top is
// kept by the session, so nothing says it is unread or never changed; and the
// function made for the input is not told it need not be `@[impure]`.  And
// what the input's last statement comes to is not dropped but shown.
#pragma once

#include <set>
#include <string>

#include "front/ast.hh"
#include "ir/types.hh"
#include "source/location.hh"
#include "util/ordered.hh"


namespace pl4g::sema {

  struct Session {
    // The function each input runs in.
    std::string function;
    // The variables of the session, by name.
    std::set<std::string, std::less<>> variables;
    // Where the names the input binds at its top are written.
    std::set<Loc> kept;
    // What each name bound in the input turned out to be, by name.
    OrderedMap<std::string, const ir::Type*> types;
    // Where each of those is put once bound, where the session keeps it: the
    // variable of the session, by the name the input bound.
    OrderedMap<std::string, std::string> stores;
    // The statement whose value is shown, what type that value turned out to
    // have, and the variable it is put in.
    const ast::Stmt* answer = nullptr;
    const ir::Type* answer_type = nullptr;
    std::string answer_variable;
  };

} // namespace pl4g::sema
