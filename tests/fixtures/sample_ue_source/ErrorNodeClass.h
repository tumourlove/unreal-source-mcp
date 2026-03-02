#pragma once
#include "CoreMinimal.h"

/**
 * This class triggers tree-sitter misparse due to ENGINE_API + multiple inheritance.
 * tree-sitter emits a declaration node instead of class_specifier.
 */
UCLASS(MinimalAPI)
class ENGINE_API UMultiInterfaceComponent : public UActorComponent, public IInterface1, public IInterface2
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable)
    void DoMultiThing();

    UPROPERTY(EditAnywhere)
    float Speed;
};
