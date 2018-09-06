#include <gtirb/Context.hpp>
#include <gtirb/DataObject.hpp>
#include <proto/DataObject.pb.h>
#include <gtest/gtest.h>

using namespace gtirb;

static Context Ctx;

TEST(Unit_DataObject, getters) {
  DataObject *D = DataObject::Create(Ctx, Addr(1), 1234);
  EXPECT_EQ(D->getAddress(), Addr(1));
  EXPECT_EQ(D->getSize(), 1234);
}

TEST(Unit_DataObject, protobufRoundTrip) {
  proto::DataObject Message;
  {
    Context InnerCtx;
    DataObject *Original = DataObject::Create(InnerCtx, Addr(1), 1234);
    Original->toProtobuf(&Message);
  }
  DataObject *Result = DataObject::fromProtobuf(Ctx, Message);

  EXPECT_EQ(Result->getAddress(), Addr(1));
  EXPECT_EQ(Result->getSize(), 1234);
}
