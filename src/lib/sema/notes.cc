// What the checker learned about the names in a file, kept for an editor.
#include "sema/notes.hh"


namespace pl4g::sema {

  const Note* Notes::at(Loc loc) const noexcept
  {
    // The narrowest, because a name may be inside another: `a.b` is a member
    // of a module and `a` is the module, and the cursor is on the smaller.
    const Note* best = nullptr;
    for (const auto& note : found) {
      if (! note.span.is_valid() || ! (note.span.start <= loc && loc < note.span.end))
        continue;
      if (best == nullptr || note.span.end - note.span.start < best->span.end - best->span.start)
        best = &note;
    }
    return best;
  }

} // namespace pl4g::sema
