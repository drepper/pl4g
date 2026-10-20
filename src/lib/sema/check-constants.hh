// The checker's methods ported from check.py lines 12196 to 15127, defined in
// check-constants.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // What the requirements of a generic function say each operation answers,
    // by the operation, its name and its operands' types.
    using Shapes = OrderedMap<std::tuple<std::string, std::string, Types>, const ir::Type*>;

    // Defined with the methods of check-places.hh, which answer it too.
    struct PlaceTaken;

    // One name a lambda brings in, and the local it is where the lambda is
    // written.
    using Taken = std::pair<const ast::Capture*, Local*>;

    // The arena `in` named for what a lambda brings in: its state, and what it
    // was made in.
    struct ArenaGiven {
      ir::Value* place = nullptr;
      KeySet made;
    };

    // Whether `written`, or a constant at the top level it names, names `name`.
    bool reaches_itself(std::string_view name, const ast::Node* written, NameSet& seen);  // check.py:12196
    // A constant at the top level that has not been defined yet, worked out from what was written.
    std::optional<Folded> constant_not_yet_defined(std::string_view name);  // check.py:12217
    // Report what is wrong with a constant, where one was needed.
    std::optional<Folded> refuse(DiagId ident, Span span, DiagArgs args = {});  // check.py:12248
    // `value` as a constant of `ty`, or refused where it does not fit.
    std::optional<Folded> fits(const ast::Expr* expr, __int128 value, const ir::IntType* ty);  // check.py:12261
    // `#x` where what `x` is says how many: an array of a stated length, a tuple, a string written down.
    std::optional<Folded> folded_length(const ast::Unary* expr);  // check.py:12269
    // An operator over two constants, worked out as the program would.
    std::optional<Folded> folded_binary(const ast::Binary* expr);  // check.py:12281
    // A constant raised to a constant power, as the program raises it: a negative exponent is one divided
    // by the positive power, toward zero, and has no answer where what was raised is nought.
    std::optional<Folded> folded_power(const ast::Expr* expr, __int128 base, __int128 exponent, const ir::IntType* ty);  // check.py:12338
    // A shift or a rotation of a constant, by a constant distance.
    std::optional<Folded> folded_shift(const ast::Binary* expr, __int128 a, __int128 b, const ir::IntType* ty);  // check.py:12348
    // A call to a pure function of this program, run while compiling.
    std::optional<Folded> folded_call(const ast::Call* expr);  // check.py:12371
    // An index as the number written down it comes to, where it is a number of no particular type known
    // while compiling.
    const ast::IntLit* index_written_down(const ast::Expr* expr);  // check.py:12417
    // What `expr` comes to while compiling, where it is a constant and is not wrong; nothing, and nothing
    // said, otherwise.
    std::optional<Folded> welcome_constant(const ast::Expr* expr);  // check.py:12425
    // The whole number an expression stands for while compiling, or nothing.
    KnownNumber constant_number(const ast::Expr* expr);  // check.py:12434
    // Lower `a⟦i…j⟧`, and `m⟦i…j, k⟧`: some of the elements, as a view.
    ir::Value* lower_slice(ir::Builder& builder, const ast::Element* expr, ir::Value* base, const ir::ArrayType* ty, const ir::Type* expected);  // check.py:12461
    // Where a run along one dimension starts, how many places it has, and how many apart they are: `i…j`
    // from `i` up to and not including `j`, and `i…j…s` every `s`-th of those.
    std::optional<std::tuple<ir::Value*, ir::Value*, ir::Value*>> run_along(ir::Builder& builder, const ast::Range* written, std::optional<std::int64_t> along, ir::Value* length, Span span);  // check.py:12518
    // Lower `a⟦i⟧ ← v`, which puts a value at one place.
    void lower_element_assign(ir::Builder& builder, const ast::ElementAssign* stmt);  // check.py:12584
    // Lower `p.x ← v`, which writes one field of a record.
    void lower_member_assign(ir::Builder& builder, const ast::MemberAssign* stmt);  // check.py:12698
    // Whether what goes into a place is asked how long it lasts (`_into_a_place`), which a record built
    // where it goes would not be.
    bool kept_by_the_place(ir::Value* where, const ast::Expr* base, const ir::Type* ty);  // check.py:12772
    // What goes into a place reached through a reference or handed over, kept as long as the place is.
    ir::Value* into_a_place(ir::Builder& builder, ir::Value* where, const ast::Expr* base, const ast::Expr* written, ir::Value* value, const ir::Type* ty, Span span, bool through = false);  // check.py:12782
    // Whether what goes into this field lasts as long as the field does.
    bool long_enough_for(ir::Value* where, ir::Value* value, const ast::MemberAssign* stmt);  // check.py:12845
    // Whether the record `expr` names may be written, said by its root.
    bool record_may_change(const ast::Expr* expr, Span where);  // check.py:12869
    // The name a call names, where it names a function held in one.
    Local* callee_value(const ast::Expr* expr);  // check.py:12937
    // Lower a call through a function held in a name.
    ir::Value* lower_indirect(ir::Builder& builder, const ast::Call* expr, Local* local, const ir::Type* expected);  // check.py:12951

    // -- lambdas -----------------------------------------------------------------

    // -- a generic function checked where it is written --------------------------
    // Check a generic function's body once, against its requirements alone.
    void check_generic(Generic* written);  // check.py:13027
    // Write down every operation the requirements name, a bundle's lines with its arguments put in,
    // settling what each arrow names.
    void requirement_shapes(std::span<const ast::Clause* const> clauses, Dict<std::string, const ir::Type*>& bound, Shapes& shapes, std::string_view name = "");  // check.py:13120
    // The type a requirement's expression stands for, writing down every operation in it on the way: what
    // it answers is the arrow's, or -- where none says -- a type nothing more may be done to.
    const ir::Type* requirement_shape(const ast::Expr* expr, const Dict<std::string, const ir::Type*>& bound, Shapes& shapes, const ir::Type* answers = nullptr);  // check.py:13173
    // An operation in a body checked where it is written: answered by the requirements where it applies to
    // a type parameter, and handed on -- operands lowered already -- where it does not.
    Held abstractly(ir::Builder& builder, const ast::Expr* expr);  // check.py:13200
    // Whether a call names a function that is no generic, no type and none of the compiler's -- one a
    // requirement can name.
    bool calls_named(const ast::Call* expr);  // check.py:13258
    // Compile the generic function for one set of types.
    ir::Function* make_instance(Generic* written, const Dict<std::string, const ir::Type*>& bound, std::span<const ir::Type* const> key, Span span);  // check.py:13269
    // Check and lower one instantiation's body, as a function's own is.
    void lower_instance(ir::Function* func, const ast::FuncDef* node, Span span);  // check.py:13358
    // Lower a call to a function whose types the call settles.
    ir::Value* lower_generic(ir::Builder& builder, const ast::Call* expr, Generic* written, const ir::Type* expected);  // check.py:13462
    // What a call of a generic function hands each parameter, in the parameters' order: what it wrote
    // there by place or by name, the default the definition gives where it wrote nothing -- or nothing,
    // for an arena it leaves to the one it stands in. Refused, and nothing, where a name is not a
    // parameter's, says one twice, or comes before a place.
    std::optional<std::vector<const ast::Expr*>> generic_arguments(const ast::Call* expr, Generic* written, std::span<const ast::Param* const> params);  // check.py:13601
    // Refuse a call whose types make a condition the compiler settles false.
    bool conditions_can_hold(Generic* written, const Dict<std::string, const ir::Type*>& bound, Span span);  // check.py:13655
    // What a parameter's type is, where everything in it is settled.
    const ir::Type* worked_out(const ast::TypeExpr* param, const Dict<std::string, const ir::Type*>& bound);  // check.py:13682
    // Read what the type parameters are out of an argument's type.
    bool reading(const ast::TypeExpr* param, const ir::Type* found, Dict<std::string, const ir::Type*>& bound, Span span, std::string_view name);  // check.py:13701
    // The definition with type parameters a type written down names.
    NamedType* template_of(const ast::TypeRef* ref);  // check.py:13768
    // A value already worked out, standing where `wanted` is asked for.
    ir::Value* as_wanted(ir::Builder& builder, ir::Value* value, const ir::Type* wanted, Span span);  // check.py:13777
    // The function this generic one comes to for these types.
    ir::Function* instance_of(Generic* written, const Dict<std::string, const ir::Type*>& bound, Span span);  // check.py:13800
    // Lower the call itself, once the function it names has been made.
    ir::Value* made_call(ir::Builder& builder, const ast::Call* expr, ir::Function* func, std::span<ir::Value* const> args, const ir::Type* expected);  // check.py:13835
    // What is written before a function type or a lambda, of the three things a caller reads off a type:
    // `listable`, `impure` and `can_ignore`. Anything else written there is reported as an attribute that
    // does not apply.
    NameSet promised(std::span<const ast::Attribute* const> attrs);  // check.py:13871
    // Whether what is written before a lambda or a function type says `listable`.
    bool walks_what_it_is_given(std::span<const ast::Attribute* const> attrs);  // check.py:13878
    // Lower `λ …`: a function written where a value is wanted.
    ir::Value* lower_lambda(ir::Builder& builder, const ast::Lambda* expr, const ir::Type* expected);  // check.py:13891
    // Everything a lambda reaches through what it brought in, as keys.
    KeySet reached_by(ir::Builder& builder, std::span<const Taken> taken);  // check.py:14067
    // What stands for the place a variable is, where a lambda reaches it.
    std::int64_t storage_key(Local* local);  // check.py:14096
    // Say in the report log where what a lambda brought in is kept.
    void report_environment(std::string_view name, std::span<const Taken> taken, bool stays, const std::optional<ArenaGiven>& arena, Span span);  // check.py:14112
    // Find every lambda in `body` that provably stays in the call writing it.
    void lambdas_that_stay(const ast::Node* body);  // check.py:14134
    // Whether `call` calls a function, rather than making a record or a variant or asking the compiler
    // something -- which keep what they are handed.
    bool calls_a_function(const ast::Call* call);  // check.py:14157
    // A named function where a value is wanted, shaped the way a lambda is.
    ir::Value* function_as_a_value(ir::Builder& builder, Held func, const ast::NameRef* ref, const ir::Type* expected);  // check.py:14173
    // The function that stands between an indirect call and `func`.
    ir::Function* shim_for(ir::Function* func, Span span);  // check.py:14234
    // What the lambda brings in, looked up where the lambda is written.
    std::optional<std::vector<Taken>> captures_of(const ast::Lambda* expr, std::string_view name);  // check.py:14279
    // What `[=]` or `[&]` brings in: everything the body reaches outside.
    std::vector<const ast::Capture*> everything_reached(const ast::Lambda* expr, std::string_view name);  // check.py:14319
    // What the environment holds for one capture.
    const ir::Type* held_by_capture(const ast::Capture* one, Local* local);  // check.py:14355
    // Room for what the lambda brings in, filled where it is written.
    std::tuple<ir::Value*, std::vector<std::int64_t>, ir::Value*, KeySet> environment(ir::Builder& builder, std::span<const Taken> taken, std::span<const ir::Type* const> held, Span span, bool stays, const std::optional<ArenaGiven>& arena);  // check.py:14373
    // What the environment holds one capture in.
    const ir::Type* slot_for(const ir::Type* what);  // check.py:14425
    // Write one capture into the room kept for it.
    void put_away(ir::Builder& builder, ir::Value* place, std::int64_t offset, const ir::Type* what, ir::Value* value, Span span);  // check.py:14435
    // Read one capture back out of the room kept for it.
    ir::Value* taken_out(ir::Builder& builder, ir::Value* place, std::int64_t offset, const ir::Type* what, Span span);  // check.py:14450
    // What a name held where the lambda was written.
    ir::Value* read_capture(ir::Builder& builder, Local* local, Span span);  // check.py:14464
    // Where one of the things brought in sits, given where they all are.
    ir::Value* inside(ir::Builder& builder, ir::Value* place, std::int64_t offset, const ir::Type* what, Span span);  // check.py:14469
    // The function a lambda's body becomes, checked and lowered.
    ir::Function* function_of_a_lambda(const ast::Lambda* expr, std::span<const ir::Type* const> params, const ir::Type* answer, std::span<const ir::Type* const> held, std::span<const std::int64_t> offsets, std::span<const Taken> taken, std::string_view name, const Dict<std::int64_t, ir::Borrowed>& borrows);  // check.py:14479
    // Report anything the list brought in that the body never reaches.
    void all_of_it_used(std::span<const Taken> brought);  // check.py:14648
    // Lower `r⌖ ← v`, which writes the place a reference names.
    void lower_deref_assign(ir::Builder& builder, const ast::DerefAssign* stmt);  // check.py:14673
    // Lower `r⌖`: what is at the place a reference names.
    ir::Value* lower_deref(ir::Builder& builder, const ast::Deref* expr, const ir::Type* expected);  // check.py:14738
    // Drop the references nothing kept, the statement that made them being over.
    void statement_ended();  // check.py:14765
    // Let the references this statement made live as long as this scope.
    void kept_by_a_name(const ir::Type* ty, Span keeper);  // check.py:14778
    // The name bound where `keeper` is, for what the log says of it.
    std::string keeper_named(Span keeper);  // check.py:14833
    // Whether `borrow` is out at the statement being lowered. One a value of a name keeps is out while
    // that value may still be what the name holds and the name is still to be used: not past the name's
    // last use, and not past where it is given another value.
    bool still_out(Borrow* borrow);  // check.py:14841
    // Write a `borrow-ended` entry for each borrow one of `values` keeps, saying why it ended there, at
    // `where`. `at` tells apart the places entries are written for, so that a body lowered twice writes
    // each once.
    void borrows_ending(const Dict<std::tuple<std::int64_t, std::int64_t>, std::string>& values, Span where, std::int64_t at);  // check.py:14868
    // The borrows that stop counting as `stmt` begins, logged above it -- which is where `_still_out`
    // stops counting them, both reading the same answers.
    void borrows_ended_at(const ast::Stmt* stmt);  // check.py:14898
    // The borrows that stop counting once `stmt` is done: one whose name it gives another value after
    // using it, and those its `return`, `break` or `continue` leaves with.
    void borrows_ended_after(const ast::Stmt* stmt);  // check.py:14907
    // The borrows still counted where `block` ends, kept by names it bound: they end with the names'
    // scope.
    void borrows_ended_with(const ast::Block* block);  // check.py:14922
    // Work out, once, which names each statement of `node` has still to use.
    void live_for(const ast::Node* node);  // check.py:14932
    // Which local's place `&expr` reaches, where it reaches one, and which part of it, as the fields down
    // to it.
    std::optional<std::tuple<std::string, std::vector<std::string>>> borrowed_name(const ast::Expr* expr);  // check.py:14951
    // The index `expr` comes to while compiling, where it is a whole number known then, asked quietly:
    // what is wrong with it is said where it is lowered, once.
    std::optional<std::int64_t> index_known(const ast::Expr* expr);  // check.py:14993
    // Take a reference of `name`'s place, refusing one that clashes.
    void lend(std::optional<std::string> name, bool mutable_, Span span, std::span<const std::string> path = {});  // check.py:15009
    // Report using `name`'s own name while a reference to it is out.
    bool lent_out(std::string_view name, Span span, bool writing, std::span<const std::string> path = {});  // check.py:15032
    // Lower `&x` and `&mut x`: a reference to the place `x` names.
    ir::Value* lower_address(ir::Builder& builder, const ast::AddressOf* expr, const ir::Type* expected);  // check.py:15051
    // Where what `expr` names is, what it holds, whether it may be written, and what to call it in a
    // message.
    std::optional<PlaceTaken> place_written(ir::Builder& builder, const ast::Expr* expr);  // check.py:15093
