#include "util/pyfloat.hh"

#include <charconv>
#include <cmath>
#include <format>


namespace pl4g {

  std::string python_repr(double value)
  {
    if (std::isnan(value))
      return "nan";
    if (std::isinf(value))
      return value < 0 ? "-inf" : "inf";
    // The shortest digits, read off the scientific form d.ddde±x.
    char buf[64];
    auto [end, ec] = std::to_chars(buf, buf + sizeof(buf), value, std::chars_format::scientific);
    std::string sci(buf, end);
    bool negative = sci.front() == '-';
    if (negative)
      sci.erase(0, 1);
    auto e = sci.find('e');
    std::string digits = sci.substr(0, e);
    int exponent = std::stoi(sci.substr(e + 1));
    if (auto point = digits.find('.'); point != std::string::npos)
      digits.erase(point, 1);
    std::string out;
    if (exponent >= -4 && exponent < 16) {
      if (exponent < 0)
        out = "0." + std::string(std::size_t(-exponent - 1), '0') + digits;
      else if (digits.size() <= std::size_t(exponent) + 1)
        out = digits + std::string(std::size_t(exponent) + 1 - digits.size(), '0') + ".0";
      else
        out = digits.substr(0, std::size_t(exponent) + 1) + "." + digits.substr(std::size_t(exponent) + 1);
    } else {
      out = digits.substr(0, 1);
      if (digits.size() > 1)
        out += "." + digits.substr(1);
      out += std::format("e{}{:02}", exponent < 0 ? '-' : '+', exponent < 0 ? -exponent : exponent);
    }
    return negative ? "-" + out : out;
  }

} // namespace pl4g
