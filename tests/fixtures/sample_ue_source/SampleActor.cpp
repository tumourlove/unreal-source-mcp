#include "SampleActor.h"
#include "Engine/World.h"

ASampleActor::ASampleActor()
    : Health(100.0f)
    , TickCount(0)
{
    PrimaryActorTick.bCanEverTick = true;
}

void ASampleActor::DoSomething(float DeltaTime)
{
    TickCount++;
    UWorld* World = GetWorld();
    if (World)
    {
        Health -= DeltaTime * 0.1f;
    }
}

float ASampleActor::GetHealth() const
{
    return Health;
}

void ASampleActor::InternalHelper()
{
    DoSomething(0.0f);
}

void FreeFunctionUsingTypes()
{
    FSampleData Data;
    Data.Value = 1.0f;
    ASampleActor* Actor = nullptr;
}
