// What a program is started with, and where the entry point puts it: the
// record the `std` module calls `Init`, in the writable data, and the names at
// the two ends of the arrangement.  A port of pypl4g/target/started.py.
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string_view>
#include <utility>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // The descriptors every process starts with open, in the order `std.Io`
  // writes them down.
  inline constexpr std::array<int, 3> inherited{ 0, 1, 2 };

  // What the record is called in the image, and where it goes.
  inline constexpr std::string_view init_symbol = "__pl4g_init";
  inline constexpr std::string_view init_section = ".data";

  // The fields of the record the entry point reaches, by name: the devices and
  // the words the program was named with, what it does its I/O with and the
  // ring among it, and the fields of a device.
  inline constexpr std::string_view devices_field = "io";
  inline constexpr std::string_view arguments_field = "args";
  inline constexpr std::string_view state_field = "fs";
  inline constexpr std::string_view ring_field = "ring";
  inline constexpr std::string_view descriptor_field = "fd";

  // What the runtime is called that waits for every request still outstanding.
  inline constexpr std::string_view drains = "pl4g_io_drain";

  // What the runtime is called that reads the arguments off the stack, and what
  // the one that reads the environment off it is called.
  inline constexpr std::string_view reads_arguments = "pl4g_args";
  inline constexpr std::string_view reads_environment = "pl4g_env";

  // What the variable holding the environment is called in the image, and what
  // the generated function that fills it is called.
  inline constexpr std::string_view environ_symbol = "__pl4g_environ";
  inline constexpr std::string_view environ_make = "__pl4g_environ_make";

  // What the table the auxiliary vector is read into is called in the image,
  // and what the generated function that reads it is called.
  inline constexpr std::string_view auxv_symbol = "__pl4g_auxv";
  inline constexpr std::string_view auxv_make = "__pl4g_auxv_make";

  // The record the startup function takes a reference to, where it takes one.
  const ir::ProductType* wanted_by(const ir::Module& module);

  // How far into the record the field `name` lies, and what it is.
  std::pair<std::uint64_t, const ir::Type*> where_in(const ir::ProductType* found, std::string_view name,
                                                     const ir::DataLayout& layout);

  // How far into the record the words it was named with go, and where the ring
  // is; nothing where the program takes no such record.
  std::optional<std::uint64_t> arguments_at(const ir::Module& module, const ir::DataLayout& layout);
  std::optional<std::uint64_t> ring_at(const ir::Module& module, const ir::DataLayout& layout);

  // Whether the entry point keeps the stack the kernel started it with: where
  // the runtime reads the arguments or the environment off it, or the reader of
  // the auxiliary vector does.
  bool holds_stack(const ir::Module& module, const ir::DataLayout& layout);

  // Whether the entry point itself calls into the packaged runtime.
  bool entry_wants_runtime(const ir::Module& module);

  // Put the record in the image, with the descriptors filled in.
  void emit_started(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout);

} // namespace pl4g::target
