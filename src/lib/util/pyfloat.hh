// A floating-point number written as Python's `repr` writes it.
//
// The shortest digits that read back as the same number, in fixed notation
// from 1e-4 up to 1e16 with at least one digit after the point, and in
// scientific notation outside that with at least two digits of exponent --
// which is what pypl4g's textual IR holds, and so what ours has to.
#pragma once

#include <string>


namespace pl4g {

  // `value` as Python's `repr(value)`.
  std::string python_repr(double value);

} // namespace pl4g
