// What happens when a contract is broken: a contract states what the code
// around it relies on, so a broken one is a defect in this implementation and
// nothing a program could have caused or recover from.  The message goes to the
// descriptor of last resort and the process stops where it is, so a debugger
// or a core file shows the state that broke it.
#include <contracts>
#include <cstdlib>
#include <format>

#include "util/fdio.hh"


void handle_contract_violation(const std::contracts::contract_violation& violation)
{
  auto where = violation.location();
  pl4g::write_all(2, std::format("{}:{}: internal error: contract violated: {}\n", where.file_name(), where.line(),
                                 violation.comment()));
  std::abort();
}
