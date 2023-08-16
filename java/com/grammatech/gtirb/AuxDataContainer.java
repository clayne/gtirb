/*
 *  Copyright (C) 2020-2021 GrammaTech, Inc.
 *
 *  This code is licensed under the MIT license. See the LICENSE file in the
 *  project root for license terms.
 *
 *  This project is sponsored by the Office of Naval Research, One Liberty
 *  Center, 875 N. Randolph Street, Arlington, VA 22203 under contract #
 *  N68335-17-C-0700.  The content of the information does not necessarily
 *  reflect the position or policy of the Government and no official
 *  endorsement should be inferred.
 *
 */

package com.grammatech.gtirb;

import com.google.protobuf.ByteString;
import com.grammatech.gtirb.proto.AuxDataOuterClass;
import java.io.*;
import java.util.*;

/**
 * Provides functionality for associating auxiliary
 * data with elements of the intermediate representation.
 */
public class AuxDataContainer extends Node {

    /**
     * Inner class for managing AuxData instances
     */
    static class AuxData {

        // Always populated.
        private String name;
        private String typeName;

        // Only populated during serialization events.
        // This is considered stale if the schema/decoded members are non-empty.
        private Optional<byte[]> encoded;

        // Only populated if the client adds/gets the AuxData.
        private Optional<AuxDataSchema> schema;
        private Optional<Object> decoded;

        /**
         * Class constructor for AuxData from protobuf AuxData.
         * @param  protoAuxData   The AuxData as serialized into a protocol
         *     buffer.
         * @param  name           The name of this AuxData.
         */
        AuxData(String name, AuxDataOuterClass.AuxData protoAuxData) {
            this.name = name;
            this.typeName = protoAuxData.getTypeName();
            this.encoded = Optional.of(protoAuxData.getData().toByteArray());
            this.schema = Optional.empty();
            this.decoded = Optional.empty();
        }

        /**
         * Class constructor for AuxData from an in-memory object.
         * @param schema The {@link AuxDataSchema} for the AuxData entry.
         * @param value The value to associate with thie AuxData entry.
         */
        <T> AuxData(AuxDataSchema<T> schema, T value) {
            this.name = schema.getName();
            this.typeName = schema.getCodec().getTypeName();
            this.encoded = Optional.empty();
            this.schema = Optional.of(schema);
            this.decoded = Optional.of(value);
        }

        /**
         * Get the AuxData name.
         *
         * @return the name.
         */
        String getName() { return this.name; }

        /**
         * Get the Type String (schemata).
         *
         * @return the type string.
         */
        String getTypeName() { return this.typeName; }

        /**
         * Get the AuxData bytes.
         *
         * @return This AuxData as a byte array.
         */
        Optional<byte[]> getEncodedData() { return this.encoded; }

        /**
         * Get the decoded form of the AuxData.
         */
        <T> T getDecodedData(AuxDataSchema<T> sch) throws Exception {
            // TODO: Some better way to confirm schema equivalence here.
            // In particular, one could have the correct name and type
            // name but still have an inconsistent type for T. Specifically,
            // we want to test if sch is equivalent to this.schema.get().

            // If this is not true, there's something seriously wrong with
            // the AuxDataContainer code.
            assert this.name == sch.getName();

            // This could be incorrect if the client is using inconsistent
            // schemas with the same schema name.
            if (!this.typeName.equals(sch.getCodec().getTypeName())) {
                throw new InconsistentAuxDataSchemaException(
                    "Schema type names do not match! " + this.typeName +
                    " vs. " + sch.getCodec().getTypeName());
            }

            if (!this.schema.isPresent()) {
                // If we're here because this is the initial get, and the
                // AuxData has not been unserialized yet. Do the decoding now.
                assert this.encoded.isPresent();
                this.schema = Optional.of(sch);
                this.decoded = Optional.of(sch.getCodec().decode(
                    new ByteArrayInputStream(this.encoded.get())));
            }

            return (T)this.decoded.get();
        }

        /**
         * Serialize this AuxData into a protobuf .
         *
         * @return AuxData protocol buffer.
         */
        AuxDataOuterClass.AuxData.Builder toProtobuf() {
            // If we have a schema and decoded object, encode first.
            if (this.schema.isPresent()) {
                assert this.decoded.isPresent();
                ByteArrayOutputStream os = new ByteArrayOutputStream();

                // ByteArrayOutputStream shouldn't ever throw, but
                // because we're passing it through the OutputStream,
                // we have a syntactic obligation to check for throws.
                try {
                    this.schema.get().getCodec().encode(os, this.decoded.get());
                } catch (Exception e) {
                    assert false;
                }

                this.encoded = Optional.of(os.toByteArray());
            } else {
                assert this.encoded.isPresent();
            }
            AuxDataOuterClass.AuxData.Builder protoAuxData =
                AuxDataOuterClass.AuxData.newBuilder();
            protoAuxData.setData(ByteString.copyFrom(this.encoded.get()));
            protoAuxData.setTypeName(this.typeName);
            return protoAuxData;
        }
    }

    protected HashMap<String, AuxData> auxDataMap;

    /**
     * Class constructor for an AuxDataContainer from a protobuf AuxData Map.
     * @param  protoUuid        The UUID of this container.
     * @param  protoAuxDataMap  A Map of AuxData names to protobuf AuxData
     * objects.
     */
    AuxDataContainer(ByteString protoUuid,
                     Map<String, AuxDataOuterClass.AuxData> protoAuxDataMap)
        throws IOException {
        super(Util.byteStringToUuid(protoUuid));
        this.auxDataMap = new HashMap<String, AuxData>();
        if (protoAuxDataMap != null) {
            for (Map.Entry<String, AuxDataOuterClass.AuxData> entry :
                 protoAuxDataMap.entrySet()) {
                AuxData ad = new AuxData(entry.getKey(), entry.getValue());
                auxDataMap.put(ad.getName(), ad);
            }
        }
    }

    AuxDataContainer() {
        super();
        this.auxDataMap = new HashMap<String, AuxData>();
    }

    /**
     * Retrieve an arbitrary {@link AuxData} item from this container if it
     * exists.
     *
     * @param name The schema for the AuxData
     * @return An {@link AuxData} object, or empty() if not present.
     */
    public <T> Optional<T> getAuxData(AuxDataSchema<T> schema)
        throws Exception {
        AuxData ad = this.auxDataMap.get(schema.getName());

        if (ad == null) {
            return Optional.empty();
        } else {
            return Optional.of(ad.getDecodedData(schema));
        }
    }

    /**
     * Adds an arbitrary {@link AuxData} item to this container. If an AuxData
     * already exists with the given schema, then it is overwritten.
     *
     * @param auxData The data to add
     */
    public <T> void putAuxData(AuxDataSchema<T> schema, T data) {
        AuxData ad = new AuxData(schema, data);
        this.auxDataMap.put(schema.getName(), ad);
    }
}
