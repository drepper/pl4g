// The checker: semantic analysis of a program, and its lowering to the IR.
//
// A port of pypl4g's sema/check.py, method for method and in the same order,
// so that what it makes of a program is instruction for instruction what
// pypl4g makes and what it says is word for word what pypl4g says.  The
// methods are spread over the check-*.cc files by the sections check.py has;
// each declaration says where in check.py it comes from.
//
// Definitions need not be processed in order: every top-level definition is
// collected first and only then is any body checked, which is what makes a
// forward reference legal.
#pragma once

#include <deque>
#include <filesystem>
#include <memory>

#include "sema/check-types.hh"
#include "sema/session.hh"
#include "sema/tables.hh"
#include "source/manager.hh"


namespace pl4g::sema {

  // What `_constant_number` answers: nothing, a refusal (Python's `False`), or
  // the number.
  struct KnownNumber {
    bool refused = false;
    std::optional<__int128> value;
  };

  // What `as_the_loops_value` puts aside and `as_it_was` puts back.
  struct LoopContext {
    std::optional<std::string> operand_of;
    std::optional<std::string> initializing;
    std::optional<std::string> assigning;
    std::optional<std::tuple<std::string, std::int64_t>> handing_over;
    bool leaving = false;
    std::optional<std::tuple<std::string, std::string>> filling;
  };

  // A slot a function type stands in (`_slot_flows`): Python's tuple key --
  // `("field", type, name)`, `("param", id, at)`, `("answer", id)`, … -- its
  // parts written out and joined by `slot_key`.
  using SlotKey = std::string;

  template<typename... Parts>
  SlotKey slot_key(const Parts&... parts)
  {
    SlotKey key;
    ((key += std::format("{}\x1f", parts)), ...);
    return key;
  }

  class Checker;

  // The names the file that wrote a definition could see, put in force for as
  // long as it lives: Python's `with self._in_scope_of(defined)`.
  class InScope {
  public:
    InScope(Checker& checker, Dict<std::string, Held>* top, Dict<std::string, Bundle*>* bundles);
    InScope(const InScope&) = delete;
    InScope& operator=(const InScope&) = delete;
    ~InScope();

  private:
    Checker& checker;
    Dict<std::string, Held>* kept_top;
    Dict<std::string, Bundle*>* kept_bundles;
  };


  // Checks one program and lowers it into a module.
  //
  // Everything is public, as everything of a Python class is: what is the
  // checker's own is everything but the constructor, `run`, `want` and
  // `quoted`, and nothing outside sema/ reaches for the rest.
  class Checker {
  public:
    Checker(ir::Module& module, DiagEngine& diags, ModuleRegistry* registry = nullptr, std::filesystem::path path = {},
            std::string prefix = "", SourceManager* sources = nullptr, std::vector<Global*>* top_level = nullptr,
            Notes* notes = nullptr, Conditions conditions = Conditions::check, bool for_macros = false,
            std::vector<const ast::Quote*>* quoted = nullptr, bool whole_modules = false, bool ignore_system = false);
    Checker(const Checker&) = delete;
    Checker& operator=(const Checker&) = delete;
    ~Checker();

    // A new object of the checker's, kept as long as the checker is: what
    // Python makes and lets the collector have.
    template<typename T, typename... Args>
    T* make(Args&&... args)
    {
      auto made = std::make_shared<T>(std::forward<Args>(args)...);
      T* raw = made.get();
      kept.push_back(std::move(made));
      return raw;
    }

    // Where the nodes the checker writes itself live.
    ast::SyntaxArena syntax;

  private:
    // What the references below refer to where nobody handed one in.
    std::vector<const ast::Quote*> own_quoted;
    std::vector<Global*> own_top_level;
    Dict<std::string, Held> own_top;
    Dict<std::string, Bundle*> own_bundles;
    std::unique_ptr<ModuleRegistry> own_registry;
    std::unique_ptr<SourceManager> own_sources;
    std::vector<std::shared_ptr<void>> kept;

  public:
    // -- state ------------------------------------------------------------------

    ir::Module& module;  // check.py:3768
    DiagEngine& diags;  // check.py:3769
    bool ignore_system = false;  // check.py:3773
    bool whole_modules = false;  // check.py:3778
    // For a module lowered as far as it is asked: the functions whose bodies
    // nothing has asked for yet, and the generic ones not yet checked where
    // they are written.
    std::vector<Collected*> waiting;  // check.py:3782
    std::vector<Generic*> waiting_generics;
    // And the variables in the image whose value is a call nothing has named
    // yet, in the order they were written: worked out where a name for one is
    // asked for, as a body is lowered.
    struct WaitingImage {
      ir::GlobalVar* var = nullptr;
      const ast::VarDef* node = nullptr;
      const ir::Type* ty = nullptr;
    };
    std::vector<std::pair<std::string, WaitingImage>> waiting_images;  // check.py:_waiting_images
    // Whether the variables are worked out where they are defined.
    bool images_now = true;
    // Work out the variable *name* in the image, left until something named it.
    void image_now(std::string_view name);  // check.py:_image_now
    // Whether the variable *name* is one waiting to be worked out.
    bool image_waits(std::string_view name) const;  // check.py:3783
    Conditions conditions = Conditions::check;  // check.py:3786
    bool for_macros = false;  // check.py:3792
    // Whether what is checked here is a module something else imported, rather
    // than the files the checker was started on: lowered as far as it is named,
    // for the macros as for the program.
    bool imported = false;  // check.py:_imported
    // The trees quoted in the macros, in the order they were written, and the
    // holes each has.  The expander reads them: a quote lowers to a call that
    // names one by its place here and fills its holes with what the macro
    // worked out.
    // Every quote a macro's body wrote, in the order the calls that name them
    // were written.  Shared with a checker reading a module, the way the
    // top-level names are: a macro in a module quotes into the same table, since
    // what runs them is one machine with one table of trees.
    // Shared with a checker reading a module: `own_quoted` where nobody shared one.
    std::vector<const ast::Quote*>& quoted;  // check.py:3801
    Notes* notes = nullptr;  // check.py:3805
    // The build function, while its body is being checked.  What a
    // compiler-provided function may be called from is that and nothing
    // else, and this is how the check knows where it is.
    ir::Function* in_build_function = nullptr;  // check.py:3809
    // Whether the body being checked is a constructor's own, which is where a
    // `@[relro]` variable may be written and nowhere else.
    bool seals_open = false;
    Dict<std::string, std::tuple<Span, std::string>> defined;  // check.py:3810
    // The variables at the top level whose value is a table rather than
    // bytes: what they are given is built by running code, so they are
    // collected here and one constructor is generated for all of them.
    std::vector<std::tuple<ir::GlobalVar*, const ast::VarDef*>> built_globals;  // check.py:3814
    // What this file's top-level names stand for.  It is the file's own,
    // not the compilation's: two files may each define a `counter`, and
    // neither can see the other's unless it imports it.
    // Pointed elsewhere while a definition of another file is checked
    // (`in_scope_of`): its names are then in force.
    Dict<std::string, Held>* top = &own_top;  // check.py:3818
    // The imports this file hands on, `@[export]` written on them: a file
    // importing this one reaches each as `this.name`.  Kept by name rather
    // than on the module, which every file importing it shares.
    NameSet exported_imports;  // check.py:3822
    // The bundles this file defines, by name.  They are beside the names
    // above rather than among them because a bundle is neither a type nor a
    // value: what it stands for is a set of requirements, and the only place
    // one may be written is a clause.
    Dict<std::string, Bundle*>* bundles = &own_bundles;  // check.py:3827
    // The function whose body is being lowered and what was written of it,
    // so that a return knows which `post` clauses to check before it.
    std::tuple<const ast::FuncDef*, ir::Function*> demanding;  // check.py:3830
    // Whose clauses have had their shapes reported on, so that a generic
    // compiled for five sets of types is refused once and not five times.
    KeySet shaped;  // check.py:3833
    // What each parameter of the function being lowered arrived as, which is
    // what `⎕entry` names.  Nothing is copied for it: a parameter's value at
    // entry is a value the function already has, and naming it here keeps it
    // alive to the return rather than making a second one.
    Dict<std::string, ir::Value*> at_entry;  // check.py:3838
    // What a program's own operators are, by the glyph and how many operands
    // it takes.  Two entries where one glyph is both prefix and infix, which
    // `⌈` is: the arity is what tells the two definitions apart, so it is part
    // of the key rather than something looked at afterwards.
    Dict<std::pair<std::string, std::int64_t>, Held> operators;  // check.py:3843
    // Where each of them came from, for the one message that has to name two
    // places: a module's name, or nothing for one written in this file.
    Dict<std::pair<std::string, std::int64_t>, std::string> operators_from;  // check.py:3846
    // What is named inside a type, by the type's name and the name after the
    // dot.  Beside the top-level names rather than among them, since a name
    // inside a type is reached only through the type: two types may each have
    // a `next` and neither reserves the word.
    Dict<std::pair<std::string, std::string>, Held> members;  // check.py:3851
    // Whether a `post` clause is being lowered just now, which is the one
    // place `⎕answer` and `⎕entry` mean anything.
    bool after = false;  // check.py:3854
    // The definitions this file owns, which carry its module's name once
    // that name is settled.
    std::vector<Held> owned;  // check.py:3857
    // The shim made for each function named where a value was wanted, so
    // that a name written twice is one shim and one symbol.
    Dict<std::int64_t, ir::Function*> shims;  // check.py:3860
    // Where this file is, which is what an import written in it is relative
    // to, and what tells its definitions from another file's.
    std::filesystem::path path;  // check.py:3863
    // Where modules are found and what has been read, shared by every file
    // of one compilation.
    ModuleRegistry* registry = nullptr;  // check.py:3866
    // The name of the module this file is, which goes in front of the name
    // of anything it imports.
    std::string prefix;  // check.py:3870
    // Where the text of a module read from here comes from.
    SourceManager* sources = nullptr;  // check.py:3872
    // Every type this file defines, in the order they were written, so
    // that one nothing names is still worked out and still reported on.
    std::vector<NamedType*> named_types;  // check.py:3875
    // The locals whose value a `match` being lowered may carry past its
    // arms, and about which the unread-value rule therefore says nothing
    // while the arms are being checked.
    KeySet carried;  // check.py:3879
    // The names the body being lowered takes a reference to, which are the
    // names given storage of their own rather than standing for a value.
    // Gathered once per body, before any of it is lowered.
    NameSet addressed;  // check.py:3883
    // How many references are open around the type being resolved.  A
    // definition that reaches itself is refused, unless a reference stands
    // somewhere on the way round.
    std::int64_t behind_a_reference;  // check.py:3887
    // The units a program has introduced, innermost scope last.  A unit
    // reaches as far as where it was written does, so the stack is pushed
    // and popped with the names.
    std::vector<Dict<std::string, const ir::Unit*>> unit_scopes{ 1 };  // check.py:3891
    // What may stand where: each pair is one `unit \N{CURRENCY SIGN}FROM \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}TO`, and the
    // relation is followed as far as it goes but never backwards.
    std::vector<std::tuple<const ir::Unit*, const ir::Unit*>> stands;  // check.py:3894
    // Whether the operands being lowered belong to a product or a quotient,
    // which are the two operators that work a unit out rather than demand
    // that both sides carry the same one.
    bool deriving = false;  // check.py:3898
    // How many lambdas have been given a name, so that the next gets one
    // nothing else has.
    std::int64_t lambdas = 0;  // check.py:3901
    // The generic function being checked where it is written, if one is.
    Abstract* abstract = nullptr;  // check.py:3903
    // The generic functions this file defined, checked once its bodies are.
    std::vector<Generic*> generics_here;  // check.py:3905
    // Whether what is being made into a constant was written `in ⎕static`,
    // which is what a value that is not one is then told about.
    bool for_the_image = false;  // check.py:3908
    // The scopes around the lambda being checked, which its body may not
    // reach.  Kept so that a name it names and did not bring in is
    // reported as one it did not bring in rather than as one nobody has.
    std::vector<Dict<std::string, Local*>*> outside;  // check.py:3912
    // What each type parameter is, while an instantiation of a generic
    // function is being checked.  Empty everywhere else, a type parameter
    // being a thing only a call gives a meaning to.
    Dict<std::string, const ir::Type*> bound;  // check.py:3916
    // Whether an array stands where one of its elements is wanted, which
    // is so while the arguments of a call to a function marked `listable`
    // are lowered and nowhere else.
    bool listing = false;  // check.py:3920
    // Whether what is being lowered stands inside `⎕wrap`, where every
    // operator answers with the low bits of what it came to rather than
    // stopping the program or stopping at the end of the type.  It is
    // lexical: it says where the operator was written and nothing about the
    // body of anything called from there.
    bool wrapping = false;  // check.py:3926
    // The bytes of each distinct string written down, so that two literals
    // saying the same thing are the one run of bytes in the image.
    // Whether the function being lowered said it may change things that
    // outlive the call.  Where it did not, the places that would make such
    // a change report one instead.
    bool impure = false;  // check.py:3932
    // Where a lambda being lowered notes that its body changes something
    // that outlives the call, rather than that being reported: a lambda says
    // nothing of its purity, and what its body does is what its type says.
    // Nothing where no lambda's body is being lowered.
    std::optional<std::vector<bool>> observing;  // check.py:3937
    // The names read through with `⌖`, by identity: a read of a `&mut` that
    // is not one of them is a copy of it, and lends it (`_lower_name`).
    KeySet read_through_ids;  // check.py:3940
    // What `Liveness` said of every statement of the functions lowered so far,
    // by the statement's identity, and which functions it has been asked of.
    Dict<std::int64_t, KeySet> live_in;  // check.py:3943
    // And as each is done, which is what says a name is not used again.
    Dict<std::int64_t, KeySet> live_out;  // check.py:3945
    // The lifetime names of the signature whose call's arguments are being
    // lowered, which stand for what that call hands over and so may be any
    // lifetime in a function type handed to it (`_answers_as_long`).
    std::optional<std::string> calling_key;  // check.py:3949
    // The names the body being lowered may change (`_changed_names`), and
    // where a `mut` that nothing needed has been said, so that it is said
    // once however many instances a body is lowered for.
    std::optional<NameSet> changed_here;  // check.py:3953
    NameSet whole_here;  // check.py:3954
    // The `⎕empty` calls the compiler put off itself, one for each arena a
    // body made, which are the only ones there are.
    KeySet implicit_empties;  // check.py:3957
    // What each function lowered here did that a mark is written for, by
    // its definition, and the record of each function the IR has.
    Dict<std::int64_t, Marks*> marks;  // check.py:3960
    Dict<std::int64_t, Marks*> marks_of;  // check.py:3961
    Marks* marking = nullptr;  // check.py:3962
    // Function types written `@[impure]` (4935): every slot one stands in
    // -- a parameter, a variable, a field, an answer -- by a key, what was
    // given to it, where its mark is written, and whether something outside
    // this unit may give it something too.
    Dict<SlotKey, std::vector<Held>> slot_flows;  // check.py:3967
    Dict<SlotKey, std::tuple<Span, std::string>> slots_marked;  // check.py:3968
    std::set<SlotKey> slots_outside;  // check.py:3969
    Dict<std::int64_t, SlotKey> slot_of_local;  // check.py:3970
    Dict<std::int64_t, const ast::FuncDef*> node_of_func;  // check.py:3971
    Dict<std::int64_t, Marks*> lambda_marks;  // check.py:3972
    // The tables written in the body being lowered whose handle arrives
    // where branches meet, and where, asked once the body is complete.
    std::vector<std::tuple<ir::Value*, Span, std::string>> unsettled_tables;  // check.py:3975
    // The generator the last `yield ⇉` handed the walk to, and where its
    // arguments are -- which is where what it answers when it ends is.
    std::optional<std::tuple<Generator*, ir::Value*>> last_delegation;  // check.py:3978
    KeySet mut_told;  // check.py:3979
    // The type a function value had before it was read as one promising
    // less, by the identity of what it was read as.
    Dict<std::int64_t, const ir::Type*> promised_as;  // check.py:3982
    // What each lambda written as an argument of that call and taking its
    // answer's lifetime from the parameter brought in that is a reference --
    // whether by reference, and the name -- by the parameter's index.
    std::optional<Dict<std::int64_t, std::vector<std::tuple<bool, Local*>>>> lambda_notes;  // check.py:3986
    // The names of the callee's signature a lambda being lowered took, which
    // what it brought in stands for in its body.
    std::set<std::tuple<std::string, std::string>> adopting;  // check.py:3989
    // The keys standing for an array in a frame of this program's, which a
    // view of it carries: what it points into goes when the array's scope
    // does.  A copy of the elements carries none of them.
    KeySet array_frames;  // check.py:3993
    // The values that are copied where they are lowered, by identity: a list
    // read out of a place into another, where either may change it in place.
    KeySet copying;  // check.py:3996
    // The subjects of a `match` that are to be read where they are, by the
    // identity of the expression, and those that were.
    KeySet match_in_place;
    KeySet matched_in_place;
    // The operators, by identity, whose answer is wanted as one run of lanes
    // held as a value rather than as an array in room of its own: what `⎕mask`
    // was handed, and every operator walked over arrays inside it.
    KeySet as_run;  // check.py:4002
    // The values that are such a run, until the operator they stand in or the
    // mask takes them.
    KeySet runs;  // check.py:4006
    Dict<std::int64_t, std::set<std::tuple<std::int64_t, std::int64_t>>> live_reach;  // check.py:3997
    Dict<std::int64_t, Dict<std::tuple<std::int64_t, std::int64_t>, std::string>> live_ends;  // check.py:3998
    Dict<std::int64_t, std::set<std::tuple<std::int64_t, std::int64_t>>> live_ends_after;  // check.py:3999
    Dict<std::int64_t, std::set<std::tuple<std::int64_t, std::int64_t>>> live_leave;  // check.py:4000
    Dict<std::int64_t, std::set<std::tuple<std::int64_t, std::int64_t>>> live_exit;  // check.py:4001
    // The statement being lowered, whose identity is what a value a name is
    // given there is told apart by.
    const ast::Stmt* current_stmt = nullptr;  // check.py:4004
    std::optional<std::set<std::tuple<std::int64_t, std::int64_t>>> current_reach;  // check.py:4005
    KeySet live_done;  // check.py:4006
    // What is live at the statement being lowered: a borrow whose keeper is
    // not among it is no longer out.  Nothing where nothing was worked out.
    std::optional<KeySet> current_live;  // check.py:4009
    // Which `borrow-ended` entries were written, so that a body lowered
    // twice writes each once.
    // Python's `(at, keeper, name, why)`, the keeper's two parts spread out.
    std::set<std::tuple<std::int64_t, std::int64_t, std::int64_t, std::string, std::string>> borrow_ends_told;  // check.py:4012
    // Whether what is being checked may touch a device.  A function handed one
    // may, which is what makes `@[impure]` mean "changes something global".
    bool may_do_io = false;  // check.py:4015
    // Whether what is being checked has actually done it, which is what makes
    // the function one a caller may not drop.
    bool did_io = false;  // check.py:4018
    // Where what is being lowered takes its room from, where `in` said: a
    // lexical thing, put in force around the expression it was written on.
    ir::Value* out_of_arena = nullptr;  // check.py:4021
    // Whether what is being lowered is what a loop comes to, which is
    // what a `break` hands over and what an `else` arm gives.  A mismatch
    // there is about the loop rather than about whatever the loop stands
    // in, so it is said as one.
    bool leaving = false;  // check.py:4026
    // The labelled loops this statement is inside, innermost last, which
    // is what `break` and `continue` look their label up in.
    std::vector<Loop*> loops;  // check.py:4029
    // The expression a loop takes its turns from while it is being lowered,
    // and what picking there left behind: picking is walked where a loop
    // takes its turns from rather than copied into an array.
    // The program's files and its functions as written, which is what a
    // call in a constant is run over.
    std::vector<const ast::SourceUnit*> units;  // check.py:4035
    bool defining_globally = false;  // check.py:4037
    bool constant_quiet = false;  // check.py:4040
    Dict<std::string, const ast::FuncDef*> definitions;  // check.py:4041
    // The variables at the top level as written, so that a constant named
    // before its definition is reached can be worked out from it.
    Dict<std::string, const ast::VarDef*> top_written;  // check.py:4044
    // The constants being worked out that way just now, which is what
    // keeps one defined in terms of itself from being asked forever.
    NameSet constants_asked;  // check.py:4047
    // The names an iterator in scope reads, which may not be written while
    // it is: the name, the iterator's, where it was made, and the depth of
    // the scope it goes with.
    std::vector<std::tuple<std::string, std::string, Span, std::int64_t>> iterator_loans;  // check.py:4051
    // The functions that take one turn of an iterator handed on, one per
    // shape of what is walked.
    Dict<std::pair<std::vector<std::int64_t>, bool>, ir::Function*> next_functions;  // check.py:4054
    // The generators whose bodies are compiled as functions, by the
    // function, and the one whose body is being lowered now -- with where
    // it writes what it hands out.
    Dict<std::int64_t, Generator*> generator_bodies;  // check.py:4058
    Generator* generating = nullptr;  // check.py:4059
    ir::Value* generating_out = nullptr;  // check.py:4060
    ir::Value* generating_frame = nullptr;  // check.py:4061
    ir::Value* generating_args = nullptr;  // check.py:4062
    // The generators being written into a loop just now, which a walk of
    // one of them inside its own body may not be.
    std::vector<std::int64_t> writing_in;  // check.py:4065
    // Which generators each generator's body walks through calls, as they
    // are found: a walk that closes a ring of them is a frame that would
    // have to hold itself, however the generators were named.
    Dict<std::int64_t, std::vector<Generator*>> walks_into;  // check.py:4069
    KeySet walks_itself_at;  // check.py:4070
    std::int64_t each_count = 0;  // check.py:4072
    const ast::Expr* pick_in = nullptr;  // check.py:4073
    // Nothing, what picking left behind (the array, its type, the mask), or a
    // walk described (`_Iterable`): Python's `_pick_walked` holds either.
    std::variant<std::monostate, std::tuple<ir::Value*, const ir::ArrayType*, Mask>, Iterable*> pick_walked;  // check.py:4074
    // What the function now being lowered answers with, which is what `?`
    // has to agree with: it leaves the function carrying an error, so the
    // function must be one that can carry it.
    const ir::Type* answering = nullptr;  // check.py:4080
    // Where each definition's name is written, for pointing at it in a note.
    Dict<std::string, Span> name_spans;  // check.py:4082
    // The names bound inside the function being checked, innermost last.
    std::vector<Dict<std::string, Local*>> scopes;  // check.py:4084
    // What each block being lowered has put off until it is left, innermost
    // last.  One list per block and not per scope, a block being what `defer`
    // is about.
    std::vector<std::vector<Deferred*>> deferred;  // check.py:4088
    // Which deferred statements have been lowered with their diagnostics
    // told.  One is lowered at every way out of its block, and a mistake in it
    // is one mistake however many ways out there are.
    KeySet defer_told;  // check.py:4092
    // Whether a deferred statement is being lowered, and if so whether for
    // the first time: what it moves is moved as the block is left, and is
    // logged once however many ways out there are.
    std::optional<std::string> in_deferred;  // check.py:4096
    // The deferred statement being lowered, and the block it runs at the
    // end of: what it does is done there, so that is where it is logged.
    std::optional<std::tuple<const ast::Defer*, Span>> deferred_at;  // check.py:4099
    // The span of each block being lowered, innermost last, beside
    // `_deferred`.
    std::vector<Span> block_spans;  // check.py:4102
    // Where a name is bound, to where the names it keeps a reference to are
    // bound, for working out where each variable's scope ends.
    Dict<std::int64_t, KeySet> lends;  // check.py:4105
    // The functions whose variables' scopes have been reported.
    KeySet scopes_told;  // check.py:4107
    // Where a variable is read as a value it is moved out of: its last use
    // there is the move, which says all there is to say about its end.
    KeySet moved_from;  // check.py:4110
    // The arenas given back in each block being lowered, by the identity of
    // their names, innermost last -- kept beside the deferred statements and
    // for the same reason: a block is what a way out leaves, and what was given
    // back on this way is what an open block says was.
    std::vector<KeySet> emptied;  // check.py:4115
    // Every arena a name is bound to, by that name's identity, so that what a
    // value records about where it was made can be asked about again.
    Dict<std::int64_t, Local*> arena_by_id;  // check.py:4118
    // Where a lambda reaches into this call -- the frame its environment is in,
    // a variable it brought in by reference -- as something standing where an
    // arena would, at the depth of the scope it goes with: a value reaching one
    // is held to the rule a value made in an arena is.  By key, as arenas are.
    Dict<std::int64_t, Local*> stand_ins;  // check.py:4123
    // The stand-in for each variable a lambda brought in by reference, by the
    // variable's identity, so that two lambdas reaching one variable say so.
    Dict<std::int64_t, std::tuple<Local*, Local*>> storage;  // check.py:4126
    // The lambdas that provably stay in the call that writes them, whose
    // environment may therefore be in its frame, by identity.
    Dict<std::int64_t, const ast::Lambda*> stays;  // check.py:4129
    // The body being lowered, until its first lambda asks which stay: most
    // bodies have none, and walking them for nothing is not free.
    const ast::Block* unscanned = nullptr;  // check.py:4132
    // Parameters whose provenance the body cannot know, by the marker they carry
    // as their arenas: a function saying where its answer was made may not
    // answer one of these, there being no saying where it was made.
    Dict<std::int64_t, Local*> unknown_by_id;  // check.py:4136
    // What the block lowered last gave back on its way off the end, which is
    // how an `if` learns what every arm reaching the join gave back.
    KeySet last_emptied;  // check.py:4139
    // What the elements of the array being filled were taken from as they were.
    KeySet element_made;  // check.py:4141
    // The allocator a call's answer keeps its elements in, by the call.
    Dict<std::int64_t, std::int64_t> holds_from;  // check.py:4143
    // Calls whose answer is certainly an object of the heap's, and not text in
    // the image the heap would be told to give back.
    KeySet fresh_heap;  // check.py:4146
    // Which function each call lowered named, for an answer that is a call of
    // the function to itself.
    Dict<std::int64_t, ir::Function*> callee_of;  // check.py:4149
    // What each answer of the body being checked turned out to be, and the
    // allocator the signature fixed for all of them, where it fixed one.
    std::vector<std::string> answer_kinds;  // check.py:4152
    std::optional<std::int64_t> thin_to;  // check.py:4153
    // The function an `if` or a `match` being lowered is the answer of, so
    // that its arms answer for themselves; and the same handed to the last
    // statement of an arm, for an `if` that ends one.
    ir::Function* answer_arms = nullptr;  // check.py:4157
    ir::Function* yield_answer = nullptr;  // check.py:4158
    // The arena of its own the body being checked hands out with its answer:
    // its name, where the caller keeps it, and how deep the body's own scope is
    // -- and the local, once `let` has made it.
    std::optional<std::tuple<std::string, ir::Value*, std::int64_t>> hands_out;  // check.py:4162
    Local* hands_out_local = nullptr;  // check.py:4163
    // The arena a definition `let v: T in kept = f(…)` receives into, until a
    // call hands it over; and the one the last such call was handed.
    KeySet hands_out_told;  // check.py:4166
    std::optional<std::tuple<Local*, const ast::Expr*>> receiving;  // check.py:4167
    Local* received = nullptr;  // check.py:4168
    // The definitions whose `in` has been checked, so a generic one says what
    // is wrong with it once and not once per instantiation.
    KeySet made_in_told;  // check.py:4171
    // Where an expression's answer was made, where lowering it said exactly: a
    // join of two strings is made in its arena and nowhere else, and a call to
    // a function saying `→ T in a` is made in what `a` was given.  The node is
    // kept beside what it says, so an id is never taken for another node's.
    Dict<std::int64_t, std::tuple<const ast::Expr*, KeySet>> made_from;  // check.py:4176
    // What was written as a list and read as a view of its elements, by the identity of what was
    // written, and the list's type: such a view reads and never writes (4251).
    Dict<std::int64_t, const ir::ListType*> list_views;
    // What the call lowered last said of its answer, for the operator form of
    // `in`, whose call is one the checker wrote and no node holds.
    std::optional<KeySet> last_made_in;  // check.py:4179
    // An `if` some of whose arms were settled while compiling, and the `if` of
    // the rest that was lowered in its place.
    Dict<std::int64_t, std::tuple<const ast::Expr*, const ast::Expr*>> made_alias;  // check.py:4182
    // The arenas `in` named around what is being lowered: `⎕heap` where none.
    KeySet out_of_arenas{ heap_key };  // check.py:4184
    // What the body being checked said its answer was made in: the arena
    // parameters' markers and how the signature named them.
    std::optional<std::tuple<KeySet, std::string>> answer_made_in;  // check.py:4187
    // The name a reference is being taken of, while one is: working the
    // place out reads the name, and that read is the reference itself.
    std::optional<std::string> taking_a_reference;
    // The allocator of the list whose element `list_element_written` last made
    // ready, which what is written into a part of it is kept in.
    ir::Value* written_allocator = nullptr;  // check.py:4190
    // The parameters each call handed the arena it stands in, having named
    // none for them, by the call's identity.
    Dict<std::int64_t, KeySet> arena_in_force;  // check.py:4193
    // The signatures around what is being lowered, innermost last, each with
    // the lifetime names it carries: a lambda's answer, or a function type's
    // in the body, may live as long as one of those.
    std::vector<std::tuple<std::string, NameSet>> lifetime_scopes;  // check.py:4197
    // What each of those names stands for in the body being lowered: the
    // parameters carrying it, as they arrived and where they are kept, with
    // the part of a tuple it is on and the parameter's name.
    Dict<std::tuple<std::string, std::string>, std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>, std::string, bool>>> lifetime_sources;  // check.py:4201
    // Which name bounds how long each value lasts, by the identity of the
    // value: a place by the name it belongs to, and a lambda by the
    // shortest-lived name it brought in by reference.  A reference is
    // walked back to one of these to find out how long what it reaches
    // lasts and what to call it.
    Dict<std::int64_t, Local*> places;  // check.py:4209
    // The references that are out, oldest first.  One is dropped when the
    // statement that made it ends, or when the scope that kept it does.
    std::vector<Borrow*> borrows;  // check.py:4212
    // The variable being given a value, while one is being checked.  It is
    // what lets a type that does not match say which variable it is about
    // rather than borrow the wording of a return.
    std::optional<std::string> initializing;  // check.py:4216
    // The variable being assigned to, while one is being checked.
    std::optional<std::string> assigning;  // check.py:4218
    // The operator whose operands are being checked, if any.  It is what
    // lets a type that does not match say which operator it is about rather
    // than borrow the wording of a return.
    std::optional<std::string> operand_of;  // check.py:4222
    // Whether anything inside the function being checked absorbed an error.
    // A construct that raises one cannot be compiled, so the definition it
    // belongs to is discarded rather than half built.
    bool discard_function = false;  // check.py:4226
    // Which argument of which function is being lowered, so that a type
    // that does not match is reported as what it is.
    std::optional<std::tuple<std::string, std::int64_t>> handing_over;  // check.py:4229
    // Which field of which record or sum is being given its value, so that a
    // type that does not match is reported as the field's.
    std::optional<std::tuple<std::string, std::string>> filling;
    // The expressions put inside something -- a record, a tuple, a list, an
    // array, a field, an element, a variable at the top level -- by what
    // they are put into, which a value that must be closed may not be.
    Dict<std::int64_t, std::string> stored_into;  // check.py:4233
    // For a name `comptime foreach` binds to a record type's field: the
    // field's type, which `f⟦1⟧` stands for.
    Dict<std::int64_t, const ir::Type*> field_types;  // check.py:4236
    // The variables already reported as never closed.
    KeySet told_unclosed;  // check.py:4238
    // By the local's identity and the span's two ends.
    std::set<std::tuple<std::int64_t, Loc, Loc>> told_after_end;  // check.py:4239
    // What each top-level definition says it raises, and where it says so.
    Dict<std::string, std::vector<Expected>> expected_pairs;  // check.py:4241
    // Every variable defined at the top level of any file, in the order it
    // was written, so that what nothing reads can be reported once every
    // file has been read.  It is shared with the checkers of the modules
    // this one imports, since the question is about the whole program.
    // Shared with the checkers of the modules this one imports.
    std::vector<Global*>& top_level;  // check.py:4246


    // -- state added while porting the methods ---------------------------------

    // The functions `discard` took out of the module, kept alive as Python
    // keeps an object something still refers to.
    std::vector<std::unique_ptr<ir::Function>> discarded;

    // What the interactive session asks beyond what a program does, where the
    // program is one of its inputs (sema/session.hh).
    Session* session = nullptr;


    // -- methods ----------------------------------------------------------------

#include "sema/check-entry.hh"
#include "sema/check-collect.hh"
#include "sema/check-arenas.hh"
#include "sema/check-constants.hh"
#include "sema/check-places.hh"
#include "sema/check-loops.hh"
#include "sema/check-match.hh"
#include "sema/check-operators.hh"
#include "sema/check-builtins.hh"
  };

} // namespace pl4g::sema
