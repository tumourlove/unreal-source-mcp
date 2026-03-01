#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SampleActor.generated.h"

/**
 * A sample actor for testing the parser.
 * Demonstrates UCLASS, UFUNCTION, UPROPERTY macros.
 */
UCLASS(BlueprintType, Blueprintable)
class ENGINE_API ASampleActor : public AActor
{
    GENERATED_BODY()

public:
    ASampleActor();

    /** Called every frame */
    UFUNCTION(BlueprintCallable, Category = "Sample")
    void DoSomething(float DeltaTime);

    /** Get the health value */
    UFUNCTION(BlueprintPure)
    float GetHealth() const;

protected:
    /** Current health of the actor */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Stats")
    float Health;

private:
    /** Internal tick counter */
    int32 TickCount;

    void InternalHelper();
};

UENUM(BlueprintType)
enum class ESampleState : uint8
{
    Idle,
    Active,
    Destroyed
};

USTRUCT(BlueprintType)
struct FSampleData
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere)
    float Value;

    UPROPERTY(EditAnywhere)
    FString Label;
};
