// Placing the packaged I/O runtime in an image.
#include "target/runtime.hh"
#include "target/emit.hh"

#include <map>
#include <set>
#include <vector>

#include "ir/builder.hh"


namespace pl4g::target {

  namespace {

    // One packaged patch, as the fixup the compiler already applies.
    mc::MCFixup as_fixup(mc::Assembler& asm_, const FixupKinds& kinds, const rt::Patch& patch, std::uint64_t first)
    {
      auto kind = kinds.find(std::string(patch.kind));
      if (kind == kinds.end()) [[unlikely]]
        throw InternalError("the runtime wants the fixup '" + std::string(patch.kind) +
                            "', which this target does not have");
      auto where = mc::sym_expr(asm_.symbol_named(piece_symbol(patch.target)));
      mc::MCFixup fixup;
      fixup.offset = std::int64_t(patch.offset - first);
      fixup.kind = kind->second;
      fixup.target = patch.addend == 0 ? where : mc::bin_expr('+', where, mc::const_expr(patch.addend));
      fixup.base_adjust = patch.measured_from;
      return fixup;
    }

    // Emit one piece, cut where a name falls so that each can be defined.
    void emit_piece(mc::Assembler& asm_, const FixupKinds& kinds, std::span<const unsigned char> contents,
                    const std::map<std::uint64_t, std::vector<std::string_view>>& inside,
                    const std::vector<const rt::Patch*>& patches)
    {
      std::set<std::uint64_t> cuts{ 0, contents.size() };
      for (const auto& [offset, _] : inside)
        cuts.insert(offset);
      std::vector<mc::MCSymbol*> opened;
      for (auto it = cuts.begin(); std::next(it) != cuts.end(); ++it) {
        auto first = *it;
        auto last = *std::next(it);
        if (auto names = inside.find(first); names != inside.end())
          for (auto name : names->second)
            opened.push_back(asm_.label(std::string(name), mc::SymBinding::local, mc::SymKind::func,
                                        mc::SymVisibility::hidden));
        std::vector<mc::MCFixup> fixups;
        for (auto one : patches)
          if (first <= one->offset && one->offset < last)
            fixups.push_back(as_fixup(asm_, kinds, *one, first));
        asm_.bytes(mc::Bytes(contents.begin() + std::ptrdiff_t(first), contents.begin() + std::ptrdiff_t(last)),
                   std::move(fixups));
      }
      for (auto one : opened)
        asm_.end_label(one);
    }

  } // anonymous namespace


  std::string piece_symbol(std::size_t index)
  {
    return ".Lpl4grt." + std::to_string(index);
  }


  void Runtime::emit(mc::Assembler& asm_, const FixupKinds& kinds) const
  {
    if (! wanted || blob == nullptr)
      return;
    std::map<std::uint32_t, std::vector<const rt::Patch*>> by_piece;
    for (const auto& patch : blob->patches)
      by_piece[patch.piece].push_back(&patch);
    // The names of each piece by where they are, in the order the blob has
    // them, which is sorted by name.
    std::map<std::uint32_t, std::map<std::uint64_t, std::vector<std::string_view>>> inside;
    for (const auto& one : blob->symbols)
      inside[one.piece][one.offset].push_back(one.name);
    for (std::size_t index = 0; index < blob->pieces.size(); ++index) {
      const auto& piece = blob->pieces[index];
      asm_.section(std::string(runtime_prefix) + std::string(piece.name),
                   placed(piece.writable, piece.alignment, piece.executable));
      asm_.align(piece.alignment);
      auto whole = asm_.label(piece_symbol(index), mc::SymBinding::local, mc::SymKind::object,
                              mc::SymVisibility::hidden);
      static const std::map<std::uint64_t, std::vector<std::string_view>> none;
      static const std::vector<const rt::Patch*> nothing;
      auto names = inside.find(std::uint32_t(index));
      auto patches = by_piece.find(std::uint32_t(index));
      emit_piece(asm_, kinds, piece.contents, names != inside.end() ? names->second : none,
                 patches != by_piece.end() ? patches->second : nothing);
      asm_.end_label(whole);
    }
  }


  bool reaches(const ir::Module& module, const Runtime& runtime)
  {
    for (const auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      for (const auto& block : func->blocks)
        for (auto inst : block->insts)
          if ((inst->kind == ir::InstKind::CallInst || inst->kind == ir::InstKind::CodeInst)
              && inst->callee != nullptr && inst->callee->is_declaration()
              && runtime.holds(ir::symbol_name(*inst->callee)))
            return true;
    }
    return false;
  }

} // namespace pl4g::target
