//===- Symbol.test.cpp ------------------------------------------*- C++ -*-===//
//
//  Copyright (C) 2018 GrammaTech, Inc.
//
//  This code is licensed under the MIT license. See the LICENSE file in the
//  project root for license terms.
//
//  This project is sponsored by the Office of Naval Research, One Liberty
//  Center, 875 N. Randolph Street, Arlington, VA 22203 under contract #
//  N68335-17-C-0700.  The content of the information does not necessarily
//  reflect the position or policy of the Government and no official
//  endorsement should be inferred.
//
//===----------------------------------------------------------------------===//
#include <gtirb/CfgNode.hpp>
#include <gtirb/CodeBlock.hpp>
#include <gtirb/Context.hpp>
#include <gtirb/DataBlock.hpp>
#include <gtirb/Module.hpp>
#include <gtirb/ProxyBlock.hpp>
#include <gtirb/Symbol.hpp>
#include <proto/Symbol.pb.h>
#include <gtest/gtest.h>

using namespace gtirb;

static Context Ctx;

TEST(Unit_Symbol, ctor_0) {
  EXPECT_NE(Symbol::Create(Ctx, nullptr, "test"), nullptr);
}

TEST(Unit_Symbol, setStorageKind) {
  const gtirb::Symbol::StorageKind Value{gtirb::Symbol::StorageKind::Static};

  auto* Node = Symbol::Create(Ctx, nullptr, "test");
  EXPECT_EQ(gtirb::Symbol::StorageKind::Extern, Node->getStorageKind());

  Node->setStorageKind(Value);
  EXPECT_EQ(Value, Node->getStorageKind());
}

TEST(Unit_Symbol, setReferent) {
  Module* Mod = Module::Create(Ctx, nullptr);
  Symbol* Sym = Mod->addSymbol(Ctx, "test");
  Section* S = Mod->addSection(Ctx, "test");
  ByteInterval* BI = S->addByteInterval(Ctx, Addr(0), 4);
  DataBlock* Data = BI->addDataBlock(Ctx, 0, 2);
  CodeBlock* B = BI->addCodeBlock(Ctx, 1, 2);
  ProxyBlock* Proxy = Mod->addProxyBlock(Ctx);

  // Symbol should have no referent yet.
  EXPECT_EQ(Sym->getReferent<Node>(), nullptr);
  EXPECT_FALSE(Sym->getAddress());

  Sym->setReferent(Data);
  EXPECT_EQ(Sym->getReferent<CodeBlock>(), nullptr);
  EXPECT_EQ(Sym->getReferent<DataBlock>(), Data);
  EXPECT_EQ(Sym->getReferent<ProxyBlock>(), nullptr);
  EXPECT_EQ(Sym->getAddress(), Addr(0));

  Sym->setReferent(B);
  EXPECT_EQ(Sym->getReferent<CodeBlock>(), B);
  EXPECT_EQ(Sym->getReferent<DataBlock>(), nullptr);
  EXPECT_EQ(Sym->getReferent<ProxyBlock>(), nullptr);
  EXPECT_EQ(Sym->getAddress(), Addr(1));

  Sym->setReferent(Proxy);
  EXPECT_EQ(Sym->getReferent<CodeBlock>(), nullptr);
  EXPECT_EQ(Sym->getReferent<DataBlock>(), nullptr);
  EXPECT_EQ(Sym->getReferent<ProxyBlock>(), Proxy);
  EXPECT_FALSE(Sym->getAddress());
}

TEST(Unit_Symbol, protobufRoundTrip) {
  proto::Symbol SMessage;
  proto::Module MMessage;
  UUID DataUUID;

  // Symbol with referent
  {
    Context InnerCtx;
    Module* Mod = Module::Create(InnerCtx, nullptr);
    Symbol* Original = Symbol::Create(InnerCtx, nullptr, "test");
    Original->setStorageKind(Symbol::StorageKind::Static);

    Section* S = Mod->addSection(InnerCtx, "test");
    ByteInterval* BI = S->addByteInterval(InnerCtx, Addr(0), 10);
    DataBlock* Data = BI->addDataBlock(InnerCtx, 1, 1);
    DataUUID = Data->getUUID();
    Original->setReferent(Data);

    Original->toProtobuf(&SMessage);

    // We must manually serialize the symbol referent. This would typically be
    // done automatically for the user when they serialized the IR.
    Mod->toProtobuf(&MMessage);
  }

  {
    Context InnerCtx;
    (void)Module::fromProtobuf(InnerCtx, nullptr, MMessage); // See above.
    Symbol* Result = Symbol::fromProtobuf(InnerCtx, nullptr, SMessage);

    EXPECT_EQ(Result->getAddress(), Addr(1));
    EXPECT_EQ(Result->getName(), "test");
    EXPECT_EQ(Result->getStorageKind(), Symbol::StorageKind::Static);
    EXPECT_EQ(Result->getReferent<DataBlock>()->getUUID(), DataUUID);
    EXPECT_EQ(Result->getReferent<CodeBlock>(), nullptr);
  }

  // Symbol with address
  {
    Context InnerCtx;
    Symbol* Original = Symbol::Create(InnerCtx, nullptr, Addr(2), "test");
    Original->toProtobuf(&SMessage);
  }

  {
    Context InnerCtx;
    Symbol* Result = Symbol::fromProtobuf(InnerCtx, nullptr, SMessage);

    EXPECT_EQ(Result->getAddress(), Addr(2));
    EXPECT_EQ(Result->getName(), "test");
    EXPECT_EQ(Result->getStorageKind(), Symbol::StorageKind::Extern);
    EXPECT_EQ(Result->getReferent<DataBlock>(), nullptr);
    EXPECT_EQ(Result->getReferent<CodeBlock>(), nullptr);
  }

  // Symbol without address
  {
    Context InnerCtx;
    Symbol* Original = Symbol::Create(InnerCtx, nullptr, "test");
    Original->toProtobuf(&SMessage);
  }

  {
    Context InnerCtx;
    Symbol* Result = Symbol::fromProtobuf(InnerCtx, nullptr, SMessage);
    EXPECT_FALSE(Result->getAddress());
    EXPECT_EQ(Result->getName(), "test");
  }
}

TEST(Unit_Symbol, visitation) {
  Symbol* Sym =
      Symbol::Create(Ctx, nullptr, CodeBlock::Create(Ctx, nullptr, 2), "test");
  Symbol* NoRef = Symbol::Create(Ctx, nullptr, "test2");

  struct Visitor {
    int operator()(CodeBlock* B) {
      // This should not be called with a null pointer.
      EXPECT_NE(B, nullptr);
      return 0;
    }
    long operator()(DataBlock*) {
      // This overload should never be called.
      EXPECT_TRUE(false);
      return 1;
    }
    long operator()(ProxyBlock*) {
      // This overload should never be called.
      EXPECT_TRUE(false);
      return 1;
    }
  };
  EXPECT_EQ(0, *Sym->visit(Visitor{}));

  // The version that has no referent should not call any of the visitor
  // functions and the returned optional should not have a value.
  struct NoRefVisitor {
    int operator()(const CfgNode*) const {
      EXPECT_TRUE(false);
      return 0;
    }
    int operator()(const DataBlock*) const {
      EXPECT_TRUE(false);
      return 1;
    }
  };
  EXPECT_FALSE(NoRef->visit(NoRefVisitor{}));

  // Similar to the test above, but ensuring we can visit without a return type.
  struct ConstVoidVisitor {
    void operator()(const CfgNode* N) const { EXPECT_NE(N, nullptr); }
    void operator()(const DataBlock*) const { EXPECT_TRUE(false); }
  };
  Sym->visit(ConstVoidVisitor{});

  // Ensure that we can provide a visitor that uses a base type.
  struct GenericVisitor {
    void operator()(const Node* N) const {
      EXPECT_NE(N, nullptr);
      // This should still only be called once.
      static int Counter;
      EXPECT_EQ(Counter++, 0);
    }
  };
  Sym->visit(GenericVisitor{});

  // Ensure that we can provide a lambda as a callable.
  Sym->visit([](const Node* N) {
    EXPECT_NE(N, nullptr);
    // This should still only be called once.
    static int Counter;
    EXPECT_EQ(Counter++, 0);
  });

  // The following is example code that should not compile. We cannot use gtest
  // to ensure that we get the appropriate compile errors, unfortunately.
  // struct NotEnoughOverloads {
  //  void operator()(const Block*) {}
  //};
  // Sym->visit(NotEnoughOverloads{}); // Error

  // struct IncorrectReturnTypes {
  //  std::string operator()(Block*) { return ""; }
  //  int operator()(DataBlock*) { return 0; }
  //};
  // Sym->visit(IncorrectReturnTypes{}); // Error
}
