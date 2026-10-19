#include "front/ast.hh"


namespace pl4g::ast {

  List<const Stmt*> Block::stmts() const
  {
    if (job != nullptr && ! done)
      settle(job->parse());
    return statements;
  }


  void Block::settle(std::optional<List<const Stmt*>> found) const
  {
    if (done)
      return;
    done = true;
    did_fail = ! found;
    statements = found.value_or(List<const Stmt*>{});
    if (job != nullptr)
      job->replay();
  }

} // namespace pl4g::ast
