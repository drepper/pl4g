// The build attributes a RISC-V image carries.
#include "target/riscv64/attributes.hh"


namespace pl4g::target::riscv64 {

  namespace {

    // The one format there is, named by the letter it begins with.
    constexpr std::uint8_t format_version = 'A';
    // Whose attributes these are.
    constexpr std::string_view vendor{ "riscv\0", 6 };
    // The scope: the whole file.
    constexpr std::uint8_t tag_file = 1;
    // Which attribute: the architecture, which carries text.
    constexpr std::uint64_t tag_arch = 5;

    void uleb128(mc::Bytes& out, std::uint64_t value)
    {
      while (true) {
        std::uint8_t byte = value & 0x7F;
        value >>= 7;
        out.push_back(byte | (value != 0 ? 0x80 : 0));
        if (value == 0)
          return;
      }
    }

    void le32(mc::Bytes& out, std::uint32_t value)
    {
      for (int i = 0; i < 4; ++i)
        out.push_back(std::uint8_t(value >> (8 * i)));
    }

  } // anonymous namespace


  mc::Bytes build_attributes(std::string_view arch)
  {
    // Built from the inside out, each length written once what it measures is
    // there.
    mc::Bytes attributes;
    uleb128(attributes, tag_arch);
    attributes.insert(attributes.end(), arch.begin(), arch.end());
    attributes.push_back(0);
    mc::Bytes scoped{ tag_file };
    le32(scoped, std::uint32_t(attributes.size() + 5));
    scoped.insert(scoped.end(), attributes.begin(), attributes.end());
    mc::Bytes out{ format_version };
    le32(out, std::uint32_t(scoped.size() + vendor.size() + 4));
    out.insert(out.end(), vendor.begin(), vendor.end());
    out.insert(out.end(), scoped.begin(), scoped.end());
    return out;
  }

} // namespace pl4g::target::riscv64
