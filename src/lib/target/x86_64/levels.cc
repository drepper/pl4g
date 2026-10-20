// The microarchitecture levels x86-64 defines.
#include "target/x86_64/levels.hh"

#include <algorithm>
#include <initializer_list>
#include <map>
#include <tuple>


namespace pl4g::target::x86_64::levels {

  namespace {

    // One mask out of the bit numbers the architecture's tables give.
    constexpr std::uint32_t bits_of(std::initializer_list<int> numbers)
    {
      std::uint32_t found = 0;
      for (int one : numbers)
        found |= std::uint32_t(1) << one;
      return found;
    }

    // What each level adds, in the order the architecture's documentation
    // lists them.  v1 is what every x86-64 processor has, so there is nothing
    // to ask about it.
    std::vector<Requirement> added(std::string_view level)
    {
      if (level == "v2")
        return {
          // SSE3, SSSE3, CMPXCHG16B, SSE4.1, SSE4.2, POPCNT
          { 1, 0, "ecx", bits_of({ 0, 9, 13, 19, 20, 23 }), { "SSE3", "SSSE3", "CMPXCHG16B", "SSE4.1", "SSE4.2", "POPCNT" } },
          // LAHF/SAHF in long mode
          { 0x80000001, 0, "ecx", bits_of({ 0 }), { "LAHF-SAHF" } },
        };
      if (level == "v3")
        return {
          // FMA, MOVBE, OSXSAVE, AVX, F16C
          { 1, 0, "ecx", bits_of({ 12, 22, 27, 28, 29 }), { "FMA", "MOVBE", "OSXSAVE", "AVX", "F16C" } },
          // BMI1, AVX2, BMI2
          { 7, 0, "ebx", bits_of({ 3, 5, 8 }), { "BMI1", "AVX2", "BMI2" } },
          // LZCNT, which the architecture counts under ABM
          { 0x80000001, 0, "ecx", bits_of({ 5 }), { "LZCNT" } },
        };
      if (level == "v4")
        return {
          // AVX512F, AVX512DQ, AVX512CD, AVX512BW, AVX512VL
          { 7, 0, "ebx", bits_of({ 16, 17, 28, 30, 31 }),
            { "AVX512F", "AVX512DQ", "AVX512CD", "AVX512BW", "AVX512VL" } },
        };
      return {};
    }

    // What each level needs the operating system to have turned on, as the
    // bits of `XCR0`: the lower and upper halves of a wide vector register for
    // v3, the mask registers and the registers AVX-512 adds for v4.
    std::pair<std::uint32_t, std::vector<std::string>> state_added(std::string_view level)
    {
      if (level == "v3")
        return { bits_of({ 1, 2 }), { "SSE", "AVX" } };
      if (level == "v4")
        return { bits_of({ 5, 6, 7 }), { "opmask", "ZMM_Hi256", "Hi16_ZMM" } };
      return { 0, {} };
    }

  } // anonymous namespace


  bool wide(std::string_view level) noexcept
  {
    return level == "v3" || level == "v4";
  }


  bool known(std::string_view level) noexcept
  {
    return std::ranges::find(names, level) != names.end();
  }


  std::vector<Requirement> requirements(std::string_view level)
  {
    std::vector<Requirement> found;
    for (auto name : names) {
      for (auto& one : added(name))
        found.push_back(std::move(one));
      if (name == level)
        break;
    }
    // One question per leaf and register, so that `CPUID` is asked once each.
    std::vector<std::tuple<std::uint32_t, std::uint32_t, std::string>> order;
    std::map<std::tuple<std::uint32_t, std::uint32_t, std::string>, Requirement> merged;
    for (auto& one : found) {
      auto key = std::tuple{ one.leaf, one.subleaf, one.reg };
      auto it = merged.find(key);
      if (it == merged.end()) {
        order.push_back(key);
        merged.emplace(key, std::move(one));
        continue;
      }
      it->second.bits |= one.bits;
      it->second.names.insert(it->second.names.end(), one.names.begin(), one.names.end());
    }
    std::vector<Requirement> answer;
    for (const auto& key : order)
      answer.push_back(merged.at(key));
    return answer;
  }


  std::uint32_t state(std::string_view level)
  {
    std::uint32_t found = 0;
    for (auto name : names) {
      found |= state_added(name).first;
      if (name == level)
        break;
    }
    return found;
  }


  std::vector<std::string> state_names(std::string_view level)
  {
    std::vector<std::string> found;
    for (auto name : names) {
      auto [_, more] = state_added(name);
      found.insert(found.end(), more.begin(), more.end());
      if (name == level)
        break;
    }
    return found;
  }


  std::string described(std::string_view level)
  {
    return "pl4g: this program was built for x86-64-" + std::string(level)
           + " and this processor does not have it\n";
  }


  std::string disabled(std::string_view level)
  {
    return "pl4g: this program was built for x86-64-" + std::string(level)
           + " and this system has not enabled the registers it uses\n";
  }

} // namespace pl4g::target::x86_64::levels
