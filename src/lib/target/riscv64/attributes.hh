// The build attributes a RISC-V image carries: pypl4g's
// target/riscv64/attributes.py.
//
// One tag is written, `Tag_RISCV_arch`, whose value is the ISA string in
// normalized form, in the format ARM invented for the job: a format byte,
// then a vendor's sub-section, then a sub-sub-section per scope.
#pragma once

#include <cstdint>
#include <string_view>

#include "mc/fixup.hh"


namespace pl4g::target::riscv64 {

  // What the section is called and its type, the processor's own.
  inline constexpr std::string_view attributes_section = ".riscv.attributes";
  inline constexpr std::uint32_t sht_riscv_attributes = 0x70000003;

  // The whole section, for a program built for `arch`.
  mc::Bytes build_attributes(std::string_view arch);

} // namespace pl4g::target::riscv64
