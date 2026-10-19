#include "ir/printer.hh"

#include <unordered_map>

#include "util/pyfloat.hh"


namespace pl4g::ir {

  std::string decimal(__int128 v)
  {
    if (v == 0)
      return "0";
    bool negative = v < 0;
    unsigned __int128 m = negative ? -(unsigned __int128)(v) : (unsigned __int128)(v);
    std::string out;
    for (; m > 0; m /= 10)
      out.insert(out.begin(), char('0' + int(m % 10)));
    return negative ? "-" + out : out;
  }


  namespace {

    // The numbers a function's values are written with: each block's
    // parameters, then each instruction with a result, in order.
    class Numbering {
    public:
      explicit Numbering(const Function& func)
      {
        unsigned counter = 0;
        for (const auto& block : func.blocks) {
          for (const auto& param : block->params)
            numbers[param.get()] = counter++;
          for (auto inst : block->insts)
            if (inst->has_result())
              numbers[inst] = counter++;
        }
      }

      std::string name_of(const Value* value) const
      {
        auto it = numbers.find(value);
        return it == numbers.end() ? std::string("%?") : "%" + std::to_string(it->second);
      }

    private:
      std::unordered_map<const Value*, unsigned> numbers;
    };


    std::string operand(const Value* value, const Numbering& numbers)
    {
      switch (value->vkind) {
      case ValueKind::global:
        return "@" + static_cast<const GlobalVar*>(value)->name;
      case ValueKind::int_const:
        return decimal(static_cast<const IntConst*>(value)->value);
      case ValueKind::char_const:
        return std::to_string(static_cast<const CharConst*>(value)->value);
      case ValueKind::bool_const:
        return static_cast<const BoolConst*>(value)->value ? "true" : "false";
      case ValueKind::float_const:
        return python_repr(static_cast<const FloatConst*>(value)->value);
      case ValueKind::undef:
        return "undef";
      case ValueKind::result_const: {
        auto result = static_cast<const ResultConst*>(value);
        return result->failed ? std::string("?") : operand(result->answer, numbers) + "?";
      }
      default:
        return numbers.name_of(value);
      }
    }

    std::string operands_of(const std::vector<Value*>& values, const Numbering& numbers)
    {
      std::string out;
      for (std::size_t i = 0; i < values.size(); ++i) {
        if (i > 0)
          out += ", ";
        out += operand(values[i], numbers);
      }
      return out;
    }

    std::string target(const BlockTarget& t, const Numbering& numbers)
    {
      std::string label = t.block != nullptr ? t.block->label : "?";
      if (t.args.empty())
        return label;
      return label + "(" + operands_of(t.args, numbers) + ")";
    }

    std::string instruction(const Instruction& inst, const Numbering& numbers)
    {
      auto operands = operands_of(inst.operands, numbers);
      switch (inst.kind) {
      case InstKind::RetInst:
        if (inst.operands.empty())
          return "ret.void";
        return "ret." + inst.operands[0]->ty->render() + " " + operands;
      case InstKind::BrInst:
        return "br " + target(inst.targets[0], numbers);
      case InstKind::CondBrInst:
        return "condbr " + operands + ", " + target(inst.targets[0], numbers) + ", " + target(inst.targets[1], numbers);
      case InstKind::UnreachableInst:
        return "unreachable";
      case InstKind::SwitchInst: {
        std::string cases;
        for (std::size_t i = 0; i < inst.cases.size(); ++i) {
          if (i > 0)
            cases += ", ";
          cases += decimal(inst.cases[i]) + " \N{RIGHTWARDS ARROW} " + target(inst.targets[i], numbers);
        }
        return "switch " + operands + ", default " + target(inst.targets.back(), numbers) + " [" + cases + "]";
      }
      case InstKind::CmpInst:
        return inst.opcode() + "." + (inst.operands.empty() ? std::string("void") : inst.operands[0]->ty->render())
               + " " + operands;
      case InstKind::MemStartInst:
        return inst.opcode();
      case InstKind::LoadInst:
        return inst.opcode() + "." + inst.ty->render() + " " + operands;
      case InstKind::StoreInst:
        return inst.opcode() + "." + (inst.operands.size() > 2 ? inst.operands[2]->ty->render() : std::string("void"))
               + " " + operands;
      case InstKind::BinaryInst:
      case InstKind::UnaryInst:
      case InstKind::CastInst:
        return inst.opcode() + "." + inst.ty->render() + " " + operands;
      default: {
        std::string out = inst.opcode() + (is<VoidType>(inst.ty) ? std::string() : "." + inst.ty->render()) + " "
                          + operands;
        while (! out.empty() && out.back() == ' ')
          out.pop_back();
        return out;
      }
      }
    }

    std::string block_header(const BasicBlock& block, const Numbering& numbers)
    {
      if (block.params.empty())
        return block.label + ":";
      std::string params;
      for (std::size_t i = 0; i < block.params.size(); ++i) {
        if (i > 0)
          params += ", ";
        params += numbers.name_of(block.params[i].get()) + ": " + block.params[i]->ty->render();
      }
      return block.label + "(" + params + "):";
    }

    std::string constant(const Value* value)
    {
      if (value == nullptr)
        return "undef";
      switch (value->vkind) {
      case ValueKind::int_const:
        return decimal(static_cast<const IntConst*>(value)->value);
      case ValueKind::char_const:
        return std::to_string(static_cast<const CharConst*>(value)->value);
      case ValueKind::bool_const:
        return static_cast<const BoolConst*>(value)->value ? "true" : "false";
      case ValueKind::float_const:
        return python_repr(static_cast<const FloatConst*>(value)->value);
      case ValueKind::result_const: {
        auto result = static_cast<const ResultConst*>(value);
        return result->failed ? std::string("?") : constant(result->answer) + "?";
      }
      default:
        return "undef";
      }
    }

    void function(const Function& func, std::string& out)
    {
      Numbering numbers(func);
      out += "fn @" + func.name + "(";
      for (std::size_t i = 0; i < func.fty->params.size(); ++i)
        out += (i > 0 ? ", " : "") + func.fty->params[i]->render();
      out += ") \N{RIGHTWARDS ARROW} " + func.fty->ret->render();
      out += " " + std::string(enum_name(func.linkage));
      out += " cconv(" + func.cconv + ")";
      if (func.attrs.special)
        out += " special(" + std::string(enum_name(*func.attrs.special)) + ")";
      if (func.attrs.priority)
        out += " priority(" + std::to_string(*func.attrs.priority) + ")";
      if (func.attrs.abi)
        out += " abi(" + *func.attrs.abi + ")";
      if (func.attrs.impure)
        out += " impure";
      if (func.attrs.opaque)
        out += " opaque";
      if (func.attrs.answer_in_storage)
        out += " answer-in-storage";
      if (func.attrs.chained)
        out += " chained";
      if (func.is_declaration()) {
        out += "\n";
        return;
      }
      out += " {\n";
      for (const auto& block : func.blocks) {
        out += block_header(*block, numbers) + "\n";
        for (auto inst : block->insts) {
          out += "  ";
          if (inst->has_result())
            out += numbers.name_of(inst) + " = ";
          out += instruction(*inst, numbers);
          if (inst->name_hint)
            out += "    ; name=" + *inst->name_hint;
          out += "\n";
        }
      }
      out += "}\n";
    }

  } // anonymous namespace


  std::string render_function(const Function& func)
  {
    std::string out;
    function(func, out);
    return out;
  }


  std::string render_module(const Module& module)
  {
    std::string out = "; pl4g-ir 1\nmodule \"" + module.name + "\" triple \"" + module.triple + "\"\n";
    if (! module.globals.empty()) {
      out += "\n";
      for (const auto& [_, var] : module.globals)
        out += "let @" + var->name + ": " + (var->is_mutable() ? "mut " : "") + var->value_type->render() + " "
               + std::string(enum_name(var->linkage)) + " = " + constant(var->initializer) + "\n";
    }
    for (const auto& [_, func] : module.functions) {
      out += "\n";
      function(*func, out);
    }
    return out;
  }

} // namespace pl4g::ir
