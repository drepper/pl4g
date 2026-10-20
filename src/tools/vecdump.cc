// Settle the runs of a module pypl4g compiled, read as JSON, for one target's
// vector registers, and print the IR it comes to -- what
// src/test/vectors_agree.py compares with what pypl4g's settle makes of it.
//
// Usage: pl4g-vecdump {none|x86-v1|x86-v3|aarch64} FILE.json
#include <argp.h>

#include <print>
#include <string>

#include "ir/json.hh"
#include "ir/printer.hh"
#include "source/manager.hh"
#include "target/vectors.hh"
#include "util/fdio.hh"


namespace {

  struct Arguments {
    std::string config;
    std::string file;
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case ARGP_KEY_ARG:
      if (args.config.empty())
        args.config = arg;
      else if (args.file.empty())
        args.file = arg;
      else [[unlikely]]
        ::argp_usage(state);
      return 0;
    case ARGP_KEY_END:
      if (args.file.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { nullptr, parse_option, "CONFIG FILE.json",
                              "Settle the runs of a module for a target's vector registers.", nullptr, nullptr,
                              nullptr };

  // What each target the test names can do to a run at once, as its back end
  // says.
  pl4g::target::Vectors configured(const std::string& name)
  {
    using pl4g::ir::BinOp;
    using pl4g::target::every_width;
    pl4g::target::Vectors made;
    if (name == "none")
      return made;
    bool aarch64 = name == "aarch64";
    std::set<unsigned> narrow{ 8, 16 };
    made.bits = name == "x86-v3" ? 256 : 128;
    made.binary = { { BinOp::and_, every_width }, { BinOp::or_, every_width }, { BinOp::xor_, every_width },
                    { BinOp::add, every_width }, { BinOp::sub, every_width },
                    { BinOp::sat_add, aarch64 ? every_width : narrow },
                    { BinOp::sat_sub, aarch64 ? every_width : narrow },
                    { BinOp::wrap_add, every_width }, { BinOp::wrap_sub, every_width },
                    { BinOp::wrap_mul, aarch64 ? std::set<unsigned>{ 8, 16, 32 }
                                       : name == "x86-v1" ? std::set<unsigned>{ 16 }
                                                          : std::set<unsigned>{ 16, 32 } } };
    made.unary = { { pl4g::ir::UnOp::not_, every_width } };
    return made;
  }

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  pl4g::SourceManager sources;
  auto read = sources.read(args.file);
  if (! read) [[unlikely]] {
    std::print(stderr, "cannot read {}\n", args.file);
    return 1;
  }
  auto module = pl4g::ir::read_json((*read)->text);
  if (! module) [[unlikely]] {
    std::print(stderr, "{}: {}\n", args.file, module.error());
    return 1;
  }
  pl4g::ir::DataLayout layout;
  layout.frames = &(*module)->frame_sizes;
  pl4g::target::settle(**module, configured(args.config), layout);
  pl4g::FdWriter out(1);
  out.print("{}", pl4g::ir::render_module(**module));
  out.flush();
}
