// An instruction of the symbolic assembler.
#include "mc/inst.hh"


namespace pl4g::mc {

  std::string MCInst::render() const
  {
    if (operands.empty())
      return desc->mnemonic;
    std::string out = desc->mnemonic + " ";
    for (std::size_t i = 0; i < operands.size(); ++i)
      out += (i > 0 ? ", " : "") + mc::render(operands[i]);
    return out;
  }


  InstRef make_inst(const InstDesc* desc, std::vector<MCOperand> operands, Span span)
  {
    auto made = std::make_shared<MCInst>();
    made->desc = desc;
    made->operands = std::move(operands);
    made->span = span;
    return made;
  }

} // namespace pl4g::mc
