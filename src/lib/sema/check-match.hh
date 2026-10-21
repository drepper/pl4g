// The checker's methods ported from check.py lines 20793 to 23412, defined in
// check-match.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // Python's `UndefConst(ty)`: a value nothing may read, a new one each time
    // as Python makes one (the module's `undef` is one per type).
    ir::Value* fresh_undef(const ir::Type* ty);
    // A declaration of a method another part defines, in the shape this part
    // calls it in: `_an_effect` takes the message's arguments.  DECLARED HERE
    // until that part declares it so.
    void an_effect(std::int64_t which, Span span, Held through, DiagArgs args);  // check.py:28387

    // -- match -------------------------------------------------------------------
    // Check and lower a `match`, which takes a value's alternatives apart.
    ir::Value* lower_match(ir::Builder& builder, const ast::Match* stmt, ir::Function* func, const ir::Type* wanted, bool produces);  // check.py:20795
    // What a value of `ty` may be: a name to report it by, what the alternative carries, and whether it is
    // a result's error.
    std::vector<std::tuple<std::string, const ir::Type*, bool>> alternatives(const ir::Type* ty);  // check.py:20824
    // Which alternatives each arm takes, or nothing where the arms are wrong.
    std::optional<std::vector<std::tuple<const ast::MatchArm*, KeySet>>> matched_arms(const ast::Match* stmt, const ir::Type* ty);  // check.py:20840
    // Which alternative a pattern takes, reporting one that takes none.
    std::optional<std::int64_t> alternative_of(const ast::Pattern* pattern, const ir::Type* ty, std::span<const std::tuple<std::string, const ir::Type*, bool>> alternatives);  // check.py:20896
    // Lower a `match` over a result, which has two alternatives.
    ir::Value* lower_match_on_result(ir::Builder& builder, const ast::Match* stmt, ir::Function* func, ir::Value* subject, const ir::ResultType* ty, std::span<const std::tuple<const ast::MatchArm*, KeySet>> taken, const ir::Type* wanted, bool produces);  // check.py:20933
    // Lower a `match` over a sum.
    ir::Value* lower_match_on_sum(ir::Builder& builder, const ast::Match* stmt, ir::Function* func, ir::Value* subject, const ir::SumType* ty, std::span<const std::tuple<const ast::MatchArm*, KeySet>> taken, const ir::Type* wanted, bool produces);  // check.py:20977
    // Lower a `match` over an enumeration.
    ir::Value* lower_match_on_enum(ir::Builder& builder, const ast::Match* stmt, ir::Function* func, ir::Value* subject, const ir::EnumType* ty, std::span<const std::tuple<const ast::MatchArm*, KeySet>> taken, const ir::Type* wanted, bool produces);  // check.py:21029
    // Lower each arm into its block and join what the arms leave behind.
    ir::Value* run_arms(ir::Builder& builder, const ast::Expr* stmt, ir::Function* func, std::span<const ArmPlan> plan, const ir::Type* wanted = nullptr, bool produces = false, ir::BasicBlock* otherwise = nullptr, ir::Value* otherwise_memory = nullptr);  // check.py:21067
    // Report a statement that is an expression whose value goes nowhere.
    void check_value_is_used(const ast::Expr* expr);  // check.py:21211
    // Report a call, standing as a statement, whose answer goes nowhere.
    void answer_is_taken(const ast::Call* expr);  // check.py:21248
    // The function a call names, asked of the syntax and reporting nothing.
    ir::Function* callee_named(const ast::Expr* expr);  // check.py:21275
    // Lower `_ ← v`: work the value out and deliberately drop it.
    ir::Value* dropped(ir::Builder& builder, const ast::AssignStmt* node, bool wants_value);  // check.py:21287
    // Lower a variable defined inside a function body.
    void lower_local(ir::Builder& builder, const ast::VarDef* node);  // check.py:21319
    // Lower a variable whose type is whatever its value turns out to be.
    // Whether `expr` is elements whose type does not state their shape: a run sliced out of an array, or a
    // name bound to one.
    bool given_a_view(const ast::Expr* expr);  // check.py:21555
    // Lower `let a: T⟦N⟧ = v⟦i…j⟧`: the elements of a view, copied into room of the name's own.
    void lower_view_copied(ir::Builder& builder, const ast::VarDef* node, const ir::ArrayType* declared);  // check.py:21570
    void lower_derived(ir::Builder& builder, const ast::VarDef* node);  // check.py:21407
    // Bind each name of a definition, or of a loop, that takes a tuple apart.
    void bind_apart(ir::Builder& builder, const ast::Node* node, ir::Value* value);  // check.py:21432
    // Record which local a computed value belongs to.
    void name_value(ir::Value* value, std::string_view name, Span where = invalid_span);  // check.py:21457
    // Lower an assignment to a variable that already exists.
    ir::Value* lower_assignment(ir::Builder& builder, const ast::AssignStmt* node, bool wants_value = false);  // check.py:21482
    // Lower `a ← v` for a name holding an array: what `v` holds is copied into the elements `a` names, and
    // `a` goes on naming them.
    ir::Value* array_assigned(ir::Builder& builder, const ast::AssignStmt* node, Local* local, bool wants_value);  // check.py:21615
    // Copy what `written` comes to into the array `into`, of type `ty`.
    bool array_into(ir::Builder& builder, const ast::Expr* written, ir::Value* into, const ir::ArrayType* ty, Span span);  // check.py:21638
    // Report an assignment to something whose definition did not allow it.
    bool check_mutable(const ast::AssignStmt* node, bool mutable_, Span where);  // check.py:21646
    // Lower an assignment that takes a tuple apart.
    void assign_apart(ir::Builder& builder, const ast::AssignStmt* node);  // check.py:21655
    // Put an already lowered value into the variable `node` names.
    void store_into(ir::Builder& builder, const ast::AssignStmt* node, ir::Value* value);  // check.py:21673
    // Lower the value of an assignment, checking it against the variable.
    ir::Value* checked_value(ir::Builder& builder, const ast::AssignStmt* node, const ir::Type* expected);  // check.py:21725
    // What a function answers with, from what its definition wrote.
    const ir::Type* return_type(const ast::TypeExpr* ref, bool views = true);  // check.py:21735
    // Lower a return statement.
    void lower_return(ir::Builder& builder, const ast::ReturnStmt* stmt, ir::Function* func);  // check.py:21765
    // Lower an expression, checking it against the expected type.
    ir::Value* lower_expr(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected);  // check.py:21814
    // Say of what `expr` puts inside itself that it is put inside something, which a value that must be
    // closed may not be (4687).
    void mark_stored(const ast::Expr* expr);  // check.py:21854
    // Whether a call of `callee` writes a record out rather than calling.
    bool makes_a_record(const ast::Expr* callee);  // check.py:21874
    // What a move of `name` here hands the value to, for the report log.
    std::string move_named(std::string_view name);  // check.py:21883
    // Say why a dead name may not be read: its arena, or its container.
    void told_gone(Local* local, std::string_view name, Span span);  // check.py:21891
    // Refuse reading part of a name whose arena was given back (4615).
    bool read_through_the_dead(const ast::Expr* expr);  // check.py:21900
    // Lower an expression as it is written: the dispatch `_lower_expr` makes.
    ir::Value* lower_written(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected);  // check.py:21918
    // Lower a comparison, whose answer is a truth value.
    ir::Value* lower_comparison(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22096
    // Lower a comparison of two strings.
    ir::Value* compared_text(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right);  // check.py:22186
    // Lower a comparison that allows for the errors floating point makes.
    ir::Value* lower_approximate(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22215
    // The variable the approximate comparisons measure against.
    ir::GlobalVar* tolerance_variable();  // check.py:22288
    // Lower a logical operator that computes both of its operands.
    ir::Value* lower_logic(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22294
    // Lower `EXPR?`: the answer, or the function leaving with the error.
    ir::Value* lower_try(ir::Builder& builder, const ast::Try* expr, const ir::Type* expected);  // check.py:22330
    // Lower `EXPR ?? DEFAULT`: the answer, or the value written instead.
    ir::Value* lower_or_else(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22403
    // Lower `and` or `or`, which do not compute the right side unless the left one leaves the answer open.
    ir::Value* lower_short_circuit(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22448
    // Lower `expr` where only a truth value will do, or report why not.
    ir::Value* boolean(ir::Builder& builder, const ast::Expr* expr, std::string_view op, bool walked = false);  // check.py:22501
    // The truth value that is true exactly where `value` is not.
    ir::Value* negate(ir::Builder& builder, ir::Value* value, Span span);  // check.py:22525
    // What one of the four operators a set answers comes to.
    ir::Value* lower_set_operation(ir::Builder& builder, ast::BinaryOp op, const ir::SetType* ty, ir::Value* left, ir::Value* right, Span span);  // check.py:22550
    // `ty` itself where it may stand on one side of `op`, and ERROR else.
    const ir::Type* comparable(Span span, ast::BinaryOp op, const ir::Type* ty);  // check.py:22580
    // Lower `SHAPE ⍴ VALUES`: something of that shape, filled with those.
    ir::Value* lower_reshape(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22626
    // The shape written on the left of the operator, or nothing where what is written is not one.
    std::optional<std::vector<std::int64_t>> written_shape(const ast::Expr* expr);  // check.py:22691
    // Lower `⊥` and `⊥ VALUE`: a result that has no answer, written out.
    ir::Value* lower_failure(ir::Builder& builder, const ast::Failure* expr, const ir::Type* expected);  // check.py:22727
    // Lower `a ∣ b`: whether `a` divides `b` with nothing left over.
    ir::Value* lower_divides(ir::Builder& builder, const ast::Expr* expr, const ast::Expr* written, const ast::Expr* over, std::string_view name, bool negated, const ir::Type* expected);  // check.py:22773
    // Whether one number divides another, where the divisor may be zero.
    ir::Value* divides_or_not(ir::Builder& builder, ir::Value* left, ir::Value* right, const ir::IntType* ty, const ir::Type* answer, bool negated, Span span);  // check.py:22868
    // Whether `left` divides `right`, as a truth value.
    ir::Value* divides(ir::Builder& builder, ir::Value* left, ir::Value* right, const ir::IntType* ty, Span span);  // check.py:22894
    // Lower `a ⁿ b`: raising by an exponent the compiler cannot see.
    ir::Value* lower_power(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:22933
    // Lower `a²`: raising by an exponent written as a raised number.
    ir::Value* lower_raised(ir::Builder& builder, const ast::Raised* expr, const ir::Type* expected);  // check.py:22986
    // Lower what is raised and what it is raised by, and check both.
    std::tuple<ir::Value*, const ir::Type*, ir::Value*, const ir::IntType*> sides_of_a_power(ir::Builder& builder, const ast::Expr* expr, const ast::Expr* written, const ast::Expr* exponent, const ir::Type* expected);  // check.py:23028
    // One divided by a power, which is what a negative exponent means.
    ir::Value* reciprocal(ir::Builder& builder, ir::Value* base, const ir::Type* ty, __int128 times, const ir::Type* answer, Span span);  // check.py:23074
    // One divided by a value, in whichever division its type has.
    ir::Value* reciprocal_of(ir::Builder& builder, ir::Value* raised, const ir::Type* ty, const ir::Type* answer, Span span);  // check.py:23089
    // Report a power of two written-down numbers that will not fit.
    bool power_is_already_known(const ast::Expr* expr, const ir::Type* ty, const ir::IntConst* base, __int128 times);  // check.py:23100
    // The value anything raised to no power at all comes to.
    ir::Value* unity(ir::Builder& builder, const ir::Type* ty, Span span);  // check.py:23119
    // Raise something to a power written down, with no loop.
    ir::Value* raised_by(ir::Builder& builder, ir::Value* base, const ir::Type* ty, __int128 times, Span span);  // check.py:23132
    // Raise something to a power the program works out, which is a loop.
    ir::Value* raised_over(ir::Builder& builder, ir::Value* base, const ir::Type* ty, ir::Value* times, const ir::IntType* count, const ir::Type* answer, Span span);  // check.py:23163
    // Squaring and multiplying, with the exponent's magnitude in a register.
    ir::Value* power_loop(ir::Builder& builder, ir::Value* base, const ir::Type* ty, ir::Value* times, const ir::IntType* count, Span span);  // check.py:23228
    // Lower `A ⧺ B`: one array's elements after another's.
    ir::Value* lower_concat(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:23287
    // Lower `A ⧺ B` where the two are lists.
    ir::Value* joined_list(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right, const ir::Type* expected);  // check.py:23332
