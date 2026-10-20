// Placing the packaged I/O runtime in an image: its bytes, a symbol for each
// name in it, and each of its patches as a fixup the target already applies.
// Emitted only where something reaches it.  A port of
// pypl4g/target/runtime.py.
#pragma once

#include <string>
#include <string_view>
#include <unordered_map>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "rt/blob.hh"


namespace pl4g::target {

  // What each piece of the runtime is called in the image: its own name with
  // this in front.
  inline constexpr std::string_view runtime_prefix = ".pl4grt";

  // What the start of the `index`th piece is called.
  std::string piece_symbol(std::size_t index);

  // The fixups a target has, by the names the packaged patches use.
  using FixupKinds = std::unordered_map<std::string, const mc::FixupKind*>;

  // The packaged runtime, and whether anything reached it.
  struct Runtime {
    const rt::Blob* blob = nullptr;
    // Whether any of it is wanted: a name is enough to want the whole of it.
    bool wanted = false;

    // Whether the runtime defines `name`.
    bool holds(std::string_view name) const noexcept { return blob != nullptr && blob->find(name).has_value(); }
    // Record that something in the program calls into it.
    void reached() noexcept { wanted = true; }
    // Put every piece into the image, with its names and its patches.
    void emit(mc::Assembler& asm_, const FixupKinds& kinds) const;
  };

  // Whether anything the program will emit calls into the runtime: asked of
  // what the functions name, not of what the module holds.
  bool reaches(const ir::Module& module, const Runtime& runtime);

} // namespace pl4g::target
